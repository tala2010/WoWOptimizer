local CANONICAL_ADDON = "RaidOptimizerStressTest"
local runtimeAddonName = ...
local ADDON = type(runtimeAddonName) == "string"
  and runtimeAddonName ~= ""
  and runtimeAddonName
  or CANONICAL_ADDON
local ADDON_VERSION = "0.10.10"
local SILENT_STANDBY_STAMP = "silent_standby_20260613"
RaidOptimizerStressTestDB = RaidOptimizerStressTestDB or {}

local db
local frame = CreateFrame("Frame")
local recorderFrame = CreateFrame("Frame")
local panel
local miniPanel
local RefreshMiniStatus
local activeTest
local recorderActive = false
local waScanHooked = false
local luaErrorHooked = false
local bugGrabberCallbackHooked = false
local fallbackErrorHandlerHooked = false
local luaErrorHandling = false
local originalErrorHandler
local bugGrabberCallbackOwner = {}
local waScanEvents = 0
local combatLogEvents = 0
local lastRecorderSample = 0
local hubRateSubscribed = false
local HUB_RATE_OWNER = "RaidOptimizerStressTest"
local detailsHooksInstalled = false
local detailsRefreshCalls = 0
local detailsRowRefreshCalls = 0
local robotProbeFrame
local robotProbeOriginal
local robotProbeWrapper
local robotProbeEndsAt = 0
local robotProbeLastTry = 0
local robotProbeCalls = 0
local robotProbeMs = 0
local robotProbeMaxMs = 0
local nameplateAdds = 0
local nameplateRemoves = 0
local autoRaidCheckGeneration = 0
local pendingContinuationOf
local BACKDROP_TEMPLATE = BackdropTemplateMixin and "BackdropTemplate" or nil
local CopyHubFlightCounters
local GetHubFlightCounters
local BuildHubFlightDelta
local CaptureCandidateIdentity

local TEST_DURATION = 30
local SAMPLE_INTERVAL = 0.5
local RECORD_SAMPLE_INTERVAL = 1
local MIN_FPS_WINDOW_MS = 500
local RECORD_MAX_SAMPLES = 7200
local LOW_FPS_THRESHOLD = 70
local NEARBY_SPELL_FPS_THRESHOLD = 90
local LOW_FPS_SPELL_WINDOW = 5
local AUTO_RAID_EXIT_GRACE = 10
local RAW_LOG_HEALTH_INTERVAL = 10
local ROBOT_PROBE_SECONDS = 5
local ROBOT_PROBE_COOLDOWN = 25
local NATIVE_SLOW_FRAME_MS = 33
local NATIVE_SLOW_FRAME_LIMIT = 2400
local NATIVE_PROFILER_TOP_K = 15
local NATIVE_PROFILER_RECENT_INTERVAL = 5
local NATIVE_PROFILER_RECENT_SAMPLE_LIMIT = 720
-- The normal five-second samples tell us who is expensive on average.  They
-- cannot prove who was expensive when a hitch actually happened.  Spike
-- samples are deliberately rare and only run in an active boss segment.
local NATIVE_PROFILER_SPIKE_COOLDOWN = 3
local NATIVE_PROFILER_SPIKE_FRAME_MS = 50

local PROFILER_METRIC_FALLBACK = {
  SessionAverageTime = 0,
  RecentAverageTime = 1,
  EncounterAverageTime = 2,
  LastTime = 3,
  PeakTime = 4,
  CountTimeOver1Ms = 5,
  CountTimeOver5Ms = 6,
  CountTimeOver10Ms = 7,
  CountTimeOver50Ms = 8,
  CountTimeOver100Ms = 9,
  CountTimeOver500Ms = 10,
  CountTimeOver1000Ms = 11,
}

local PROFILER_COUNTER_METRICS = {
  "CountTimeOver1Ms",
  "CountTimeOver5Ms",
  "CountTimeOver10Ms",
  "CountTimeOver50Ms",
  "CountTimeOver100Ms",
}

local BLOODLUST_SPELLS = {
  [2825] = "嗜血",
  [32182] = "英勇",
  [80353] = "时间扭曲",
  [90355] = "远古狂乱",
  [160452] = "虚空之风",
  [178207] = "狂怒战鼓",
  [230935] = "上古战鼓",
  [264667] = "原始暴怒",
  [381301] = "荒蛮鼓声",
  [390386] = "狂怒嗥叫",
}

local WATCHED_PRESSURE_SPELLS = {
  [15290] = "吸血鬼的拥抱",
  [52042] = "治疗之泉图腾",
  [58433] = "乱射",
  [52212] = "枯萎凋零",
  [54968] = "圣光鞘",
  [50475] = "鲜血灵气",
  [24932] = "兽群领袖",
  [48161] = "真言术：韧",
  [63944] = "新生希望",
  [54181] = "邪能共效",
  [70940] = "神圣护卫者",
  [57723] = "筋疲力尽",
  [57724] = "心满意足",
  [57623] = "寒冬号角",
  [55053] = "死亡之花",
  [55593] = "死亡之花",
  [1303618] = "暗言术：渊",
  [1295167] = "混沌流星",
  [15258] = "暗影交织/神爵圣契",
  [1303678] = "光环死神蜂巢",
}

local function CleanReportText(text)
  text = tostring(text or "")
  text = text:gsub("[%z\1-\31\127]", "")
  text = text:gsub("�", "")
  text = text:gsub("^%s+", ""):gsub("%s+$", "")
  return text
end

local function NonEmptyText(text, fallback)
  text = CleanReportText(text)
  if text == "" then return fallback or "其他" end
  return text
end

local function CleanSpellName(spellId, spellName)
  local watchedName = WATCHED_PRESSURE_SPELLS[spellId]
  if watchedName then return watchedName end
  local cleaned = CleanReportText(spellName)
  if cleaned == "" then return "未知技能" end
  return cleaned
end

local function Band(value, mask)
  if bit and bit.band then return bit.band(value or 0, mask or 0) end
  if bit32 and bit32.band then return bit32.band(value or 0, mask or 0) end
  return 0
end

local function IsFriendlyPlayerSource(sourceFlags)
  local playerFlag = _G.COMBATLOG_OBJECT_TYPE_PLAYER or 0x00000400
  local mineFlag = _G.COMBATLOG_OBJECT_AFFILIATION_MINE or 0x00000001
  local partyFlag = _G.COMBATLOG_OBJECT_AFFILIATION_PARTY or 0x00000002
  local raidFlag = _G.COMBATLOG_OBJECT_AFFILIATION_RAID or 0x00000004
  if Band(sourceFlags, playerFlag) == 0 then return false end
  return Band(sourceFlags, mineFlag + partyFlag + raidFlag) ~= 0
end

local STORED_SPELL_EVENTS = {
  SPELL_CAST_START = true,
  SPELL_CAST_SUCCESS = true,
  SPELL_DAMAGE = true,
  SPELL_PERIODIC_DAMAGE = true,
  SPELL_HEAL = true,
  SPELL_PERIODIC_HEAL = true,
  SPELL_AURA_APPLIED = true,
  SPELL_AURA_REFRESH = true,
  SPELL_AURA_REMOVED = true,
  SPELL_AURA_APPLIED_DOSE = true,
  SPELL_AURA_REMOVED_DOSE = true,
  SPELL_SUMMON = true,
  SPELL_ENERGIZE = true,
  SPELL_PERIODIC_ENERGIZE = true,
}

local function ShouldStoreSpellEvent(subevent)
  return subevent and STORED_SPELL_EVENTS[subevent] or false
end

local function SpellEventCategory(subevent, spellName)
  local name = tostring(spellName or "")
  if name:find("药膏") or name:lower():find("poison") then return "毒药" end
  if subevent and subevent:find("ENERGIZE", 1, true) then return "资源" end
  if subevent == "SPELL_PERIODIC_DAMAGE" or subevent == "SPELL_PERIODIC_HEAL" then return "周期" end
  if subevent and subevent:find("SPELL_AURA_", 1, true) == 1 then return "光环" end
  if subevent == "SPELL_HEAL" then return "治疗" end
  if subevent == "SPELL_SUMMON" then return "召唤" end
  if subevent == "SPELL_CAST_START" or subevent == "SPELL_CAST_SUCCESS" then return "施法" end
  if subevent and subevent:find("SWING") then return "近战" end
  if subevent and subevent:find("RANGE") then return "远程" end
  if subevent and subevent:find("DAMAGE") then return "伤害" end
  if subevent and subevent:find("HEAL") then return "治疗" end
  if subevent and subevent:find("MISS") then return "未命中" end
  return "伤害"
end

local function SpellRiskLabel(spellId)
  if WATCHED_PRESSURE_SPELLS[spellId] then
    return "重点"
  end
  return nil
end

local function SourceScopeLabel(sourceFlags)
  local playerFlag = _G.COMBATLOG_OBJECT_TYPE_PLAYER or 0x00000400
  local mineFlag = _G.COMBATLOG_OBJECT_AFFILIATION_MINE or 0x00000001
  local partyFlag = _G.COMBATLOG_OBJECT_AFFILIATION_PARTY or 0x00000002
  local raidFlag = _G.COMBATLOG_OBJECT_AFFILIATION_RAID or 0x00000004
  local hostileFlag = _G.COMBATLOG_OBJECT_REACTION_HOSTILE or 0x00000040
  if Band(sourceFlags, playerFlag) ~= 0 then
    if Band(sourceFlags, mineFlag) ~= 0 then return "自己" end
    if Band(sourceFlags, partyFlag + raidFlag) ~= 0 then return "队友" end
    if Band(sourceFlags, hostileFlag) ~= 0 then return "敌方玩家" end
    return "玩家"
  end
  if Band(sourceFlags, hostileFlag) ~= 0 then return "敌方/NPC" end
  return "其他"
end

local function CopyCounter(counter)
  local out = {}
  for key, value in pairs(counter or {}) do
    out[key] = value or 0
  end
  return out
end

local function DeltaCounter(counter, previous)
  local rows = {}
  for key, value in pairs(counter or {}) do
    local delta = (value or 0) - ((previous and previous[key]) or 0)
    if delta > 0 then
      rows[#rows + 1] = { label = key, count = delta }
    end
  end
  table.sort(rows, function(a, b) return (a.count or 0) > (b.count or 0) end)
  return rows
end

local function FormatCounterRows(rows, limit)
  local lines = {}
  for index = 1, math.min(limit or 6, #(rows or {})) do
    local row = rows[index]
    lines[#lines + 1] = string.format("%s %d", row.label or "其他", row.count or 0)
  end
  if #lines == 0 then return "无" end
  return table.concat(lines, "；")
end

local function AddSpellTotal(record, category, spellId, spellName, sourceName)
  if not record or not spellId or not spellName then return end
  spellName = CleanSpellName(spellId, spellName)
  record.spellTotals = record.spellTotals or {}
  local key = tostring(spellId or 0) .. "\0" .. tostring(spellName or "") .. "\0" .. tostring(category or "")
  local row = record.spellTotals[key]
  if not row then
    row = {
      spellId = spellId or 0,
      spellName = spellName or "未知技能",
      category = category or "其他",
      count = 0,
      sourceName = sourceName or "",
    }
    record.spellTotals[key] = row
  end
  row.count = (row.count or 0) + 1
  if sourceName and sourceName ~= "" then
    row.sourceName = sourceName
  end
end

local function FormatTopSpellTotals(record, limit)
  local rows = {}
  for _, row in pairs((record and record.spellTotals) or {}) do
    rows[#rows + 1] = row
  end
  table.sort(rows, function(a, b) return (a.count or 0) > (b.count or 0) end)
  local lines = {}
  for index = 1, math.min(limit or 8, #rows) do
    local row = rows[index]
    local category = NonEmptyText(row.category, "其他")
    local risk = SpellRiskLabel(row.spellId)
    if risk then category = category .. "/" .. risk end
    lines[#lines + 1] = string.format(
      "- [%s]%s(%s) x%d",
      category,
      row.spellName or "未知技能",
      tostring(row.spellId or 0),
      row.count or 0
    )
  end
  if #lines == 0 then return "- 未抓到可归类技能。" end
  return table.concat(lines, "\n")
end

local function FormatTopSpellTotalsBrief(record, limit)
  local rows = {}
  for _, row in pairs((record and record.spellTotals) or {}) do
    rows[#rows + 1] = row
  end
  table.sort(rows, function(a, b) return (a.count or 0) > (b.count or 0) end)
  local lines = {}
  for index = 1, math.min(limit or 5, #rows) do
    local row = rows[index]
    local label = NonEmptyText(row.spellName, "未知技能")
    local risk = SpellRiskLabel(row.spellId)
    if risk then label = label .. "*" end
    lines[#lines + 1] = string.format("%s(%s) x%d", label, tostring(row.spellId or 0), row.count or 0)
  end
  if #lines == 0 then return "未抓到可归类技能" end
  return table.concat(lines, "；")
end

local function TrimTopSpellText(text, limit)
  if not text or text == "" then return "无" end
  local out = {}
  for rawItem in string.gmatch(text, "([^；]+)") do
    local item = CleanReportText(rawItem)
    if item ~= "" then
    out[#out + 1] = item
    if #out >= (limit or 3) then break end
    end
  end
  if #out == 0 then return text end
  return table.concat(out, "；")
end

-- Automatic diagnosis stays invisible in customer gameplay. Results are kept
-- locally for the desktop support/diagnostic flow.
local function Print(_) end

local function Now()
  return GetTime and GetTime() or 0
end

local function AddLine(text)
  if not panel or not panel.output then return end
  panel.output:SetText(text or "")
  if panel.scrollChild and panel.output.GetStringHeight then
    local height = math.max(900, (panel.output:GetStringHeight() or 0) + 24)
    panel.scrollChild:SetHeight(height)
  end
  if panel.scroll then
    panel.scroll:UpdateScrollChildRect()
    panel.scroll:SetVerticalScroll(0)
  end
end

local function SafeCall(fn, ...)
  if type(fn) ~= "function" then return nil end
  local ok, a, b, c, d, e, f, g, h = pcall(fn, ...)
  if ok then return a, b, c, d, e, f, g, h end
  return nil
end

local function IsFiniteNumber(value)
  return type(value) == "number"
    and value == value
    and value > -math.huge
    and value < math.huge
end

local function PreciseNow()
  if type(GetTimePreciseSec) == "function" then
    local ok, value = pcall(GetTimePreciseSec)
    if ok and IsFiniteNumber(value) then return value, "GetTimePreciseSec" end
  end
  return Now(), "GetTime"
end

local function UnixNow()
  if type(time) == "function" then
    local ok, value = pcall(time)
    if ok and IsFiniteNumber(value) then return value end
  end
  return nil
end

local function ServerUnixNow()
  if type(GetServerTime) == "function" then
    local ok, value = pcall(GetServerTime)
    if ok and IsFiniteNumber(value) then return value end
  end
  return nil
end

local function CaptureClockAnchor(record, kind)
  if type(record) ~= "table" then return nil end
  local monotonicSec, source = PreciseNow()
  record.clockAnchorCount = (record.clockAnchorCount or 0) + 1
  local relativeSec = monotonicSec - (record.startedMonotonicSec or monotonicSec)
  local anchor = {
    sequence = record.clockAnchorCount,
    kind = tostring(kind or "anchor"),
    relativeSec = relativeSec,
    monotonicSec = monotonicSec,
    monotonicSource = source,
    unixSec = UnixNow(),
    serverUnixSec = ServerUnixNow(),
    localText = date("%Y-%m-%d %H:%M:%S"),
  }
  record.clockAnchors = record.clockAnchors or {}
  record.clockAnchors[#record.clockAnchors + 1] = anchor
  return anchor
end

local function ProfilerMetricId(name)
  local enum = type(Enum) == "table" and Enum.AddOnProfilerMetric or nil
  local value = type(enum) == "table" and enum[name] or nil
  if type(value) == "number" then return value end
  return PROFILER_METRIC_FALLBACK[name]
end

local function ReadProfilerMetric(methodName, metricName)
  local profiler = type(C_AddOnProfiler) == "table" and C_AddOnProfiler or nil
  local method = profiler and profiler[methodName] or nil
  local metric = ProfilerMetricId(metricName)
  if type(method) ~= "function" or type(metric) ~= "number" then return nil end
  local ok, value = pcall(method, metric)
  if ok and IsFiniteNumber(value) and value >= 0 then return value end
  return nil
end

local function CopyProfilerTopK(metricName, limit)
  local profiler = type(C_AddOnProfiler) == "table" and C_AddOnProfiler or nil
  local method = profiler and profiler.GetTopKAddOnsForMetric or nil
  local metric = ProfilerMetricId(metricName)
  if type(method) ~= "function" or type(metric) ~= "number" then return nil, "unavailable" end
  local ok, raw = pcall(method, metric, limit or NATIVE_PROFILER_TOP_K)
  if not ok then return nil, "error" end
  if type(raw) ~= "table" then return nil, "invalid" end
  if type(raw.results) == "table" then raw = raw.results end
  local rows = {}
  for _, value in pairs(raw) do
    if type(value) == "table" then
      local name = value.addOnName or value.addonName or value.name or value[1]
      local metricValue = value.metricValue or value.value or value[2]
      if name ~= nil and IsFiniteNumber(metricValue) and metricValue >= 0 then
        rows[#rows + 1] = {
          addOnName = tostring(name),
          metricValue = metricValue,
        }
      end
    end
  end
  table.sort(rows, function(a, b) return a.metricValue > b.metricValue end)
  while #rows > (limit or NATIVE_PROFILER_TOP_K) do table.remove(rows) end
  return rows, #rows > 0 and "ok" or "empty"
end

local function ProbeNativeProfiler()
  local profiler = type(C_AddOnProfiler) == "table" and C_AddOnProfiler or nil
  local capability = {
    namespace = profiler and true or false,
    application = profiler and type(profiler.GetApplicationMetric) == "function" or false,
    overall = profiler and type(profiler.GetOverallMetric) == "function" or false,
    topK = profiler and type(profiler.GetTopKAddOnsForMetric) == "function" or false,
    ticks = profiler and type(profiler.GetTicksPerSecond) == "function" or false,
    cpuBound = type(IsCpuBound) == "function",
    reportedEnabled = nil,
    available = false,
  }
  if profiler and type(profiler.IsEnabled) == "function" then
    local ok, enabled = pcall(profiler.IsEnabled)
    if ok then capability.reportedEnabled = enabled and true or false end
  end
  local probe = ReadProfilerMetric("GetOverallMetric", "SessionAverageTime")
  if probe == nil then
    probe = ReadProfilerMetric("GetApplicationMetric", "SessionAverageTime")
  end
  capability.available = probe ~= nil
  capability.probeValue = probe
  if capability.ticks then
    local ok, ticks = pcall(profiler.GetTicksPerSecond)
    if ok and IsFiniteNumber(ticks) and ticks > 0 then capability.ticksPerSecond = ticks end
  end
  return capability
end

local function CaptureNativeProfilerSnapshot(record, phase)
  local capability = record and record.nativeProfilerCapability or nil
  if type(capability) ~= "table" or not capability.available then return nil end
  local started = type(debugprofilestop) == "function" and debugprofilestop() or nil
  local snapshot = {
    phase = tostring(phase or "snapshot"),
    atSec = record.elapsedSec or 0,
    application = {},
    overall = {},
  }
  local metricNames
  if phase == "start" then
    metricNames = {
      "PeakTime",
      "CountTimeOver1Ms",
      "CountTimeOver5Ms",
      "CountTimeOver10Ms",
      "CountTimeOver50Ms",
      "CountTimeOver100Ms",
    }
  else
    metricNames = {
      "EncounterAverageTime",
      "RecentAverageTime",
      "LastTime",
      "PeakTime",
      "CountTimeOver1Ms",
      "CountTimeOver5Ms",
      "CountTimeOver10Ms",
      "CountTimeOver50Ms",
      "CountTimeOver100Ms",
    }
  end
  for _, metricName in ipairs(metricNames) do
    snapshot.application[metricName] = ReadProfilerMetric("GetApplicationMetric", metricName)
    snapshot.overall[metricName] = ReadProfilerMetric("GetOverallMetric", metricName)
  end
  if phase ~= "start" and capability.topK then
    local encounterRows, encounterStatus = CopyProfilerTopK("EncounterAverageTime", NATIVE_PROFILER_TOP_K)
    local recentRows, recentStatus = CopyProfilerTopK("RecentAverageTime", NATIVE_PROFILER_TOP_K)
    snapshot.topAddOnsEncounter = encounterRows or {}
    snapshot.topAddOnsEncounterStatus = encounterStatus
    snapshot.topAddOnsRecent = recentRows or {}
    snapshot.topAddOnsRecentStatus = recentStatus
    if encounterStatus == "ok" then
      snapshot.topAddOns = encounterRows
      snapshot.topAddOnsSource = "EncounterAverageTime"
    elseif recentStatus == "ok" then
      snapshot.topAddOns = recentRows
      snapshot.topAddOnsSource = "RecentAverageTime"
    else
      snapshot.topAddOns = encounterRows or recentRows or {}
      snapshot.topAddOnsSource = encounterRows and "EncounterAverageTime" or "RecentAverageTime"
    end
    snapshot.topAddOnsPeak = CopyProfilerTopK("PeakTime", NATIVE_PROFILER_TOP_K)
    snapshot.topAddOnsOver5Ms = CopyProfilerTopK("CountTimeOver5Ms", NATIVE_PROFILER_TOP_K)
    snapshot.topAddOnsOver10Ms = CopyProfilerTopK("CountTimeOver10Ms", NATIVE_PROFILER_TOP_K)
  end
  if started and type(debugprofilestop) == "function" then
    snapshot.captureMs = math.max(0, debugprofilestop() - started)
  end
  return snapshot
end

local function CounterDelta(before, after)
  if not IsFiniteNumber(before) or not IsFiniteNumber(after) then return nil, false end
  if after < before then return nil, true end
  return after - before, false
end

local function FinalizeNativeProfilerBoss(boss)
  if type(boss) ~= "table" then return end
  local before = boss.nativeProfilerStart
  local after = boss.nativeProfilerEnd
  if type(after) ~= "table" then return end
  boss.nativeProfilerCounters = { application = {}, overall = {}, resetDetected = false }
  for _, metricName in ipairs(PROFILER_COUNTER_METRICS) do
    local appDelta, appReset = CounterDelta(
      before and before.application and before.application[metricName],
      after.application and after.application[metricName]
    )
    local addonDelta, addonReset = CounterDelta(
      before and before.overall and before.overall[metricName],
      after.overall and after.overall[metricName]
    )
    boss.nativeProfilerCounters.application[metricName] = appDelta
    boss.nativeProfilerCounters.overall[metricName] = addonDelta
    if appReset or addonReset then boss.nativeProfilerCounters.resetDetected = true end
  end
  local rawAppMs = after.application and after.application.EncounterAverageTime
  local rawAddonMs = after.overall and after.overall.EncounterAverageTime
  local recentAppMs = after.application and after.application.RecentAverageTime
  local recentAddonMs = after.overall and after.overall.RecentAverageTime
  boss.applicationEncounterRawMs = rawAppMs
  boss.addonEncounterRawMs = rawAddonMs
  boss.applicationRecentMs = recentAppMs
  boss.addonRecentMs = recentAddonMs
  local appMs = rawAppMs
  local addonMs = rawAddonMs
  boss.nativeProfilerAverageSource = "EncounterAverageTime"
  if not IsFiniteNumber(appMs) or appMs <= 0 or not IsFiniteNumber(addonMs) then
    if IsFiniteNumber(recentAppMs) and recentAppMs > 0 and IsFiniteNumber(recentAddonMs) then
      appMs = recentAppMs
      addonMs = recentAddonMs
      boss.nativeProfilerAverageSource = "RecentAverageTime"
    end
  end
  if IsFiniteNumber(appMs) and IsFiniteNumber(addonMs) then
    boss.applicationEncounterMs = appMs
    boss.addonEncounterMs = addonMs
    boss.nonAddonEncounterMs = math.max(0, appMs - addonMs)
    if appMs > 0 then boss.addonShare = math.max(0, math.min(1, addonMs / appMs)) end
  end
  local beforePeak = before and before.application and before.application.PeakTime
  local afterPeak = after.application and after.application.PeakTime
  if IsFiniteNumber(beforePeak) and IsFiniteNumber(afterPeak) then
    boss.newSessionPeak = afterPeak > beforePeak
  end
end

local function TrimList(list, limit)
  while #list > limit do
    table.remove(list, 1)
  end
end

local CurrentBossSegment

local function ExtractLuaErrorAddon(errorText, stackText)
  local text = tostring(errorText or "") .. "\n" .. tostring(stackText or "")
  local namedAddon = text:match("插件%s*'([^']+)'")
    or text:match('[Aa]dd[oO]n%s*\"([^\"]+)\"')
    or text:match("[Aa]dd[oO]n%s*'([^']+)'")
  if namedAddon and namedAddon ~= "" then return namedAddon end
  local ignored = {
    ["!BugGrabber"] = true,
    BugSack = true,
    RaidOptimizerStressTest = true,
  }
  local patterns = {
    "[Ii]nterface[\\/]+AddOns[\\/]+([^\\/:%s]+)",
    "@[Ii]nterface[\\/]+AddOns[\\/]+([^\\/:%s]+)",
    "%.%.%.[\\/]+AddOns[\\/]+([^\\/:%s]+)",
  }
  for _, pattern in ipairs(patterns) do
    for addon in text:gmatch(pattern) do
      if addon and addon ~= "" and not ignored[addon] then
        return addon
      end
    end
  end
  return "未知来源"
end

local function ShortLuaError(errorText)
  local text = CleanReportText(errorText)
  text = text:gsub("[Ii]nterface[\\/]+AddOns[\\/]+", "")
  text = text:gsub("%s+", " ")
  if #text > 110 then
    text = text:sub(1, 110) .. "..."
  end
  if text == "" then text = "未知 Lua 错误" end
  return text
end

local function AddCounterValue(counter, key, amount)
  if not counter or not key then return end
  counter[key] = (counter[key] or 0) + (amount or 1)
end

local function RecordLuaError(errorText, providedStack, providedAddon)
  if luaErrorHandling then return end
  if not recorderActive or not db or not db.raidRecord or not db.raidRecord.active then return end
  luaErrorHandling = true
  pcall(function()
    local record = db.raidRecord
    local stackText = providedStack
    if stackText == nil then stackText = debugstack and SafeCall(debugstack, 3, 5, 0) or "" end
    local addon = providedAddon or ExtractLuaErrorAddon(errorText, stackText)
    local short = ShortLuaError(errorText)
    record.luaErrorCount = (record.luaErrorCount or 0) + 1
    record.luaErrorAddons = record.luaErrorAddons or {}
    record.luaErrorMessages = record.luaErrorMessages or {}
    record.luaErrorRecent = record.luaErrorRecent or {}
    AddCounterValue(record.luaErrorAddons, addon, 1)
    AddCounterValue(record.luaErrorMessages, addon .. "： " .. short, 1)
    record.luaErrorRecent[#record.luaErrorRecent + 1] = {
      t = math.floor(Now() - (record.startedAt or Now())),
      addon = addon,
      message = short,
    }
    TrimList(record.luaErrorRecent, 80)
    local boss = CurrentBossSegment(record)
    if boss then
      boss.luaErrorCount = (boss.luaErrorCount or 0) + 1
      boss.luaErrorAddons = boss.luaErrorAddons or {}
      AddCounterValue(boss.luaErrorAddons, addon, 1)
    end
  end)
  luaErrorHandling = false
end

local function TryHookBugGrabber()
  if bugGrabberCallbackHooked then return true end
  if type(EventRegistry) ~= "table" or type(EventRegistry.RegisterCallback) ~= "function"
    or type(BugGrabber) ~= "table" or type(BugGrabber.GetErrorByID) ~= "function"
  then
    return false
  end
  local function OnBugGrabbed(_, errorID)
    local ok, errorObject = pcall(BugGrabber.GetErrorByID, BugGrabber, errorID)
    if not ok or type(errorObject) ~= "table" then return end
    RecordLuaError(errorObject.message or "未知 Lua 错误", errorObject.stack or "")
  end
  local ok = pcall(
    EventRegistry.RegisterCallback,
    EventRegistry,
    "BugGrabber.BugGrabbed",
    OnBugGrabbed,
    bugGrabberCallbackOwner
  )
  bugGrabberCallbackHooked = ok and true or false
  luaErrorHooked = bugGrabberCallbackHooked or fallbackErrorHandlerHooked
  return bugGrabberCallbackHooked
end

local function HookLuaErrorHandler()
  -- BugGrabber deliberately replaces the public seterrorhandler with a no-op.
  -- Its callback is therefore the authoritative path when available and also
  -- includes ADDON_ACTION_BLOCKED/FORBIDDEN events that are not Lua throws.
  if TryHookBugGrabber() then return end
  if fallbackErrorHandlerHooked or not geterrorhandler or not seterrorhandler then return end
  local ok, handler = pcall(geterrorhandler)
  if not ok then return end
  if type(handler) ~= "function" then return end
  originalErrorHandler = handler
  local function raidOptimizerErrorHandler(errorText)
    if not bugGrabberCallbackHooked then RecordLuaError(errorText) end
    local handled, result = pcall(originalErrorHandler, errorText)
    if handled then return result end
  end
  local setOk = pcall(seterrorhandler, raidOptimizerErrorHandler)
  local verifyOk, installed = pcall(geterrorhandler)
  fallbackErrorHandlerHooked = setOk and verifyOk and installed == raidOptimizerErrorHandler
  luaErrorHooked = bugGrabberCallbackHooked or fallbackErrorHandlerHooked
end

local function GetAddonCPU()
  if not UpdateAddOnCPUUsage or not GetNumAddOns or not GetAddOnInfo or not GetAddOnCPUUsage then
    return 0, {}
  end
  SafeCall(UpdateAddOnCPUUsage)
  local total = 0
  local rows = {}
  for index = 1, GetNumAddOns() do
    local name, _, _, enabled = GetAddOnInfo(index)
    if enabled then
      local cpu = GetAddOnCPUUsage(index) or 0
      total = total + cpu
      if cpu > 0 then
        rows[#rows + 1] = { name = name or ("Addon" .. index), cpu = cpu }
      end
    end
  end
  table.sort(rows, function(a, b) return a.cpu > b.cpu end)
  return total, rows
end

local function GetAddonMemory()
  if not UpdateAddOnMemoryUsage or not GetNumAddOns or not GetAddOnInfo or not GetAddOnMemoryUsage then
    return 0, {}
  end
  SafeCall(UpdateAddOnMemoryUsage)
  local total = 0
  local rows = {}
  for index = 1, GetNumAddOns() do
    local name, _, _, enabled = GetAddOnInfo(index)
    if enabled then
      local memory = GetAddOnMemoryUsage(index) or 0
      total = total + memory
      if memory > 1 then
        rows[#rows + 1] = { name = name or ("Addon" .. index), memory = memory }
      end
    end
  end
  table.sort(rows, function(a, b) return a.memory > b.memory end)
  return total, rows
end

local function CpuProfileEnabled()
  local value = SafeCall(GetCVar, "scriptProfile")
  return tostring(value or "0") == "1"
end

local function EnableCpuProfile()
  if CpuProfileEnabled() then
    Print("旧性能记录开关已经开启。正式测帧数建议关闭后 /reload，避免拖低 FPS。")
    return
  end
  local ok = SafeCall(SetCVar, "scriptProfile", "1")
  if ok == false then
    Print("开启旧性能记录开关失败，请检查客户端是否支持 scriptProfile。")
    return
  end
  Print("已尝试开启旧性能记录开关。通常需要 /reload 或重启游戏后才会生效。")
end

local function DisableCpuProfile()
  if not CpuProfileEnabled() then
    Print("旧性能记录开关已经关闭。")
    return
  end
  local ok = SafeCall(SetCVar, "scriptProfile", "0")
  if ok == false then
    Print("关闭旧性能记录开关失败，请检查客户端是否支持 scriptProfile。")
    return
  end
  Print("已关闭旧性能记录开关。请 /reload 或重启游戏后再做正式 FPS 测试。")
end

local function CountVisibleNameplates()
  if not C_NamePlate or not C_NamePlate.GetNamePlates then return 0 end
  local plates = C_NamePlate.GetNamePlates() or {}
  return #plates
end

local function NameplateScanWork()
  if not C_NamePlate or not C_NamePlate.GetNamePlates then return 0 end
  local plates = C_NamePlate.GetNamePlates() or {}
  local reads = 0
  for repeatIndex = 1, 8 do
    for _, plate in ipairs(plates) do
      local unit = plate.namePlateUnitToken
      if unit then
        SafeCall(UnitName, unit)
        SafeCall(UnitGUID, unit)
        SafeCall(UnitHealth, unit)
        SafeCall(UnitHealthMax, unit)
        SafeCall(UnitCastingInfo, unit)
        SafeCall(UnitChannelInfo, unit)
        SafeCall(UnitAffectingCombat, unit)
        for auraIndex = 1, 8 do
          SafeCall(UnitAura, unit, auraIndex, "HELPFUL")
          SafeCall(UnitAura, unit, auraIndex, "HARMFUL")
        end
        reads = reads + 1
      end
    end
  end
  return reads
end

local function FireWAEvents()
  local count = 0
  if WeakAuras and WeakAuras.ScanEvents then
    for index = 1, 80 do
      WeakAuras.ScanEvents("RAID_OPTIMIZER_STRESS", index, "SPELL_AURA_APPLIED", "raid" .. ((index % 40) + 1), 66012 + index)
      WeakAuras.ScanEvents("COMBAT_LOG_EVENT_UNFILTERED", Now(), "SPELL_DAMAGE", false, "sourceGUID", "sourceName", 0, 0, "destGUID", "destName", 0, 0, 66012 + index, "StressSpell", 1, 1000 + index, 0, 1, nil, nil, nil, false, false, false)
      count = count + 2
    end
  else
    for index = 1, 2000 do
      count = count + (index % 7)
    end
  end
  return count
end

local function ModeLabel(mode)
  if mode == "baseline" then return "基线测试" end
  if mode == "wa" then return "WA/事件压力" end
  if mode == "nameplate" then return "姓名板/单位扫描压力" end
  if mode == "combined" then return "综合团本压力" end
  return mode or "未知测试"
end

local function FormatTopCPU(rows, startRows)
  local startMap = {}
  for _, item in ipairs(startRows or {}) do
    startMap[item.name] = item.cpu
  end
  local deltas = {}
  for _, item in ipairs(rows or {}) do
    local delta = item.cpu - (startMap[item.name] or 0)
    if delta > 0.01 then
      deltas[#deltas + 1] = { name = item.name, delta = delta }
    end
  end
  table.sort(deltas, function(a, b) return a.delta > b.delta end)
  local lines = {}
  for index = 1, math.min(6, #deltas) do
    lines[#lines + 1] = string.format("- %s: %.1f ms", deltas[index].name, deltas[index].delta)
  end
  if #lines == 0 then
    lines[1] = "- 未采到明显插件性能增量。"
  end
  return table.concat(lines, "\n")
end

local function FormatTopMemory(rows)
  local lines = {}
  for index = 1, math.min(6, #(rows or {})) do
    lines[#lines + 1] = string.format("- %s: %.1f MB", rows[index].name, (rows[index].memory or 0) / 1024)
  end
  if #lines == 0 then
    lines[1] = "- 未采到明显插件内存数据。"
  end
  return table.concat(lines, "\n")
end

local function GetLibGetFrameStats()
  local major = "LibGetFrame-1.0"
  local channel = "公共"
  if RaidOptimizerHub and type(RaidOptimizerHub.GetWeakAurasFrameResolverStatus) == "function" then
    local okStatus, status = pcall(RaidOptimizerHub.GetWeakAurasFrameResolverStatus)
    if okStatus and type(status) == "table" and status.ready and status.major then
      major = status.major
      channel = "WA私有"
    end
  end
  local ok, lib = pcall(function()
    return LibStub and LibStub(major, true)
  end)
  if not ok or not lib or not lib.GetStats then return nil end
  local okStats, stats = pcall(lib.GetStats)
  if okStats and type(stats) == "table" then
    local copy = {}
    for key, value in pairs(stats) do copy[key] = value end
    copy.raidOptimizerChannel = channel
    return copy
  end
  return nil
end

local function AddonLoaded(addonName)
  if C_AddOns and C_AddOns.IsAddOnLoaded then
    local ok, loaded = pcall(C_AddOns.IsAddOnLoaded, addonName)
    if ok then return loaded and true or false end
  end
  if IsAddOnLoaded then
    local ok, loaded = pcall(IsAddOnLoaded, addonName)
    if ok then return loaded and true or false end
  end
  return false
end

local function AddonVersion(addonName)
  if C_AddOns and C_AddOns.GetAddOnMetadata then
    local ok, version = pcall(C_AddOns.GetAddOnMetadata, addonName, "Version")
    if ok and version then return version end
  end
  if GetAddOnMetadata then
    local ok, version = pcall(GetAddOnMetadata, addonName, "Version")
    if ok and version then return version end
  end
  return "未知"
end

local function LoadedText(addonName)
  return AddonLoaded(addonName) and "已加载" or "未加载"
end

local function WAFrameRouteSafetyText()
  -- The current Titan candidate deliberately leaves WeakAuras' frame resolver
  -- native.  A private replacement can change a custom aura's returned frame
  -- and therefore its anchor/scale.  It must never be reported as a failed
  -- takeover merely because that intentionally-absent private library is not
  -- loaded.
  return "WA框体路径：保留 WeakAuras 原生框体库（不改头像周围 WA、位置、大小或缩放）"
end

local function HubEventBridgeText()
  local identity = CaptureCandidateIdentity and CaptureCandidateIdentity() or nil
  if type(identity) == "table" and identity.hubAvailable
    and tostring(identity.mode or "native") ~= "central"
  then
    return "底层事件桥：中枢当前为 native，未接管任何事件；已安全回退原生插件路径"
  end
  local counters = GetHubFlightCounters and GetHubFlightCounters() or nil
  if type(counters) ~= "table" then
    return "底层事件桥：中枢未加载，未接管任何事件"
  end
  local wa = tonumber(counters.waConsumerCount) or 0
  local ndui = tonumber(counters.nduiConsumerCount) or 0
  local bindings = tonumber(counters.boundFrames) or 0
  if wa > 0 or ndui > 0 or bindings > 0 then
    return string.format(
      "底层事件桥：已接管 WA %d 条、NDui %d 条活动路由；绑定框体 %d（仅精确匹配，其他保持原生）",
      wa, ndui, bindings)
  end
  return "底层事件桥：当前没有可安全接管的活动路由；非匹配 WA/NDui 保持原生，不是失败"
end

RaidOptimizerStressTestNDuiAuraWatchRouteText = function()
  if not RaidOptimizerHub or type(RaidOptimizerHub.GetMode) ~= "function"
    or RaidOptimizerHub.GetMode() ~= "central"
    or type(RaidOptimizerHub.GetUnitDataStats) ~= "function"
  then
    return "NDui AuraWatch：未绑定中枢，保持原生"
  end
  local ok, stats = pcall(RaidOptimizerHub.GetUnitDataStats)
  if not ok or type(stats) ~= "table" then
    return "NDui AuraWatch：中枢统计不可用，保持原生"
  end
  local queries = tonumber(stats.legacyAuraListQueries) or 0
  local hits = tonumber(stats.legacyAuraListHits) or 0
  local fallbacks = tonumber(stats.legacyAuraListFallbacks) or 0
  if queries == 0 then
    return "NDui AuraWatch：等待真实调用（尚未确认绑定）"
  end
  return string.format(
    "NDui AuraWatch：已绑定中枢；缓存命中 %d/%d，原生回退 %d",
    hits, queries, fallbacks)
end

RaidOptimizerStressTestNDuiRaidAuraProbeText = function()
  if type(RaidOptimizerNDuiRaidAuraProbeSnapshot) ~= "function" then
    return "NDui团队光环探针：未加载"
  end
  local ok, stats = pcall(RaidOptimizerNDuiRaidAuraProbeSnapshot)
  if not ok or type(stats) ~= "table" then
    return "NDui团队光环探针：状态不可用"
  end
  local frames = tonumber(stats.wrappedFrames) or 0
  local calls = tonumber(stats.calls) or 0
  if frames == 0 then
    return "NDui团队光环探针：等待团队框架（不接管、不改显示）"
  end
  return string.format(
    "NDui团队光环探针：已绑定 %d 个活动团队框体，观察到 %d 次刷新（只测量、不接管）",
    frames, calls)
end

local function CandidateTelemetryReadinessText()
  local identity = CaptureCandidateIdentity and CaptureCandidateIdentity() or nil
  local counters = GetHubFlightCounters and GetHubFlightCounters() or nil
  if type(identity) == "table" and identity.mode == "wa-52111-local"
    and identity.releaseId == RaidOptimizerStressTestLocalWAReleaseId and type(counters) == "table"
  then
    return "B组逐Boss遥测：已就绪（" .. identity.releaseId
      .. "，WA精准扫描 " .. tostring(tonumber(counters.waAuraSelectiveScans) or 0)
      .. "，原生回退 " .. tostring(tonumber(counters.waAuraNativeFallbackScans) or 0) .. "）"
  end
  if type(identity) == "table" and identity.hubAvailable
    and tostring(identity.mode or "") == "central"
    and type(counters) == "table" and identity.releaseId ~= ""
    and (tonumber(counters.boundFrames) or 0) > 0
  then
    return "B组逐Boss遥测：已就绪（" .. tostring(identity.releaseId)
      .. "，模式 " .. tostring(identity.mode or "unknown") .. "）"
  end
  if type(identity) == "table" and identity.hubAvailable
    and tostring(identity.mode or "native") ~= "central"
  then
    return "B组逐Boss遥测：未就绪（中枢当前为 native）；不要开始正式B组副本测试"
  end
  return "B组逐Boss遥测：未就绪；中枢未 central 或没有真实绑定时不要开始正式B组副本测试"
end

local function RuntimeValidationText()
  return table.concat({
    "- 流畅度诊断：" .. LoadedText(ADDON) .. "，版本 " .. tostring(AddonVersion(ADDON)),
    "- " .. WAFrameRouteSafetyText(),
    "- " .. HubEventBridgeText(),
    "- " .. RaidOptimizerStressTestNDuiAuraWatchRouteText(),
    "- " .. RaidOptimizerStressTestNDuiRaidAuraProbeText(),
    "- " .. CandidateTelemetryReadinessText(),
    "- 大众版默认验证项：流畅度诊断 + WA底层框体库；旧减负/Details/姓名板伴随包不作为当前大众版加载项。",
  }, "\n")
end

local function LoadedVersionBrief(addonName, label)
  local loaded = AddonLoaded(addonName) and "已加载" or "未加载"
  return label .. tostring(AddonVersion(addonName)) .. loaded
end

local function RuntimeValidationBriefText()
  return table.concat({
    LoadedVersionBrief(ADDON, "诊断"),
    HubEventBridgeText(),
  }, "；")
end

local function FormatLibGetFrameStats(stats)
  if not stats then return "未检测到 LibGetFrame 统计。" end
  local queries = stats.queries or 0
  local cacheHits = stats.cacheHits or 0
  local hitRate = queries > 0 and (cacheHits * 100 / queries) or 0
  return string.format(
    "LibGetFrame（%s通道）：查询 %d，缓存命中 %.1f%%，目标短缓存 %d，失败短缓存 %d，已知直达 %d，索引命中 %d，未命中 %d，扫描 %d，枚举 %d",
    tostring(stats.raidOptimizerChannel or "未知"),
    queries,
    hitRate,
    stats.normalizeCacheHits or 0,
    stats.negativeCacheHits or 0,
    stats.knownGlobalHits or 0,
    stats.indexHits or 0,
    stats.misses or 0,
    stats.scans or 0,
    stats.framesSeen or 0
  )
end

local function HookWeakAurasScanEvents()
  if waScanHooked or not WeakAuras or not WeakAuras.ScanEvents then return end
  local original = WeakAuras.ScanEvents
  if type(original) ~= "function" then return end
  WeakAuras.ScanEvents = function(...)
    if recorderActive then
      waScanEvents = waScanEvents + 1
    end
    return original(...)
  end
  waScanHooked = true
end

local function CopyTopCpuDelta(rows, startRows, limit)
  local startMap = {}
  for _, item in ipairs(startRows or {}) do
    startMap[item.name] = item.cpu
  end
  local deltas = {}
  for _, item in ipairs(rows or {}) do
    local delta = item.cpu - (startMap[item.name] or 0)
    if delta > 0.01 then
      deltas[#deltas + 1] = { name = item.name, delta = delta }
    end
  end
  table.sort(deltas, function(a, b) return a.delta > b.delta end)
  local out = {}
  for index = 1, math.min(limit or 5, #deltas) do
    out[#out + 1] = deltas[index]
  end
  return out
end

local function AddCpuAggregate(record, rows, startRows)
  if not record or not rows then return end
  local startMap = {}
  for _, item in ipairs(startRows or {}) do
    startMap[item.name] = item.cpu
  end
  record.cpuTotals = record.cpuTotals or {}
  record.cpuPeaks = record.cpuPeaks or {}
  for _, item in ipairs(rows or {}) do
    local delta = item.cpu - (startMap[item.name] or 0)
    if delta > 0.01 then
      record.cpuTotals[item.name] = (record.cpuTotals[item.name] or 0) + delta
      record.cpuPeaks[item.name] = math.max(record.cpuPeaks[item.name] or 0, delta)
    end
  end
end

local function FormatCpuDeltaRows(rows)
  local lines = {}
  for index = 1, math.min(5, #(rows or {})) do
    lines[#lines + 1] = string.format("%s %.1fms", rows[index].name, rows[index].delta or 0)
  end
  if #lines == 0 then return "无明显性能增量" end
  return table.concat(lines, "；")
end

local function FormatCpuAggregate(record)
  if not record or not record.cpuTotals then
    return "未开启内部归因。"
  end
  local rows = {}
  for name, total in pairs(record.cpuTotals or {}) do
    rows[#rows + 1] = {
      name = name,
      total = total or 0,
      peak = (record.cpuPeaks and record.cpuPeaks[name]) or 0,
    }
  end
  table.sort(rows, function(a, b) return (a.total or 0) > (b.total or 0) end)
  local lines = {}
  for index = 1, math.min(10, #rows) do
    lines[#lines + 1] = string.format(
      "- %s：累计 %.1fms，单次峰值 %.1fms",
      rows[index].name or "未知插件",
      rows[index].total or 0,
      rows[index].peak or 0
    )
  end
  if #lines == 0 then
    return "已开启内部归因，但没有采到明显插件性能增量。"
  end
  return table.concat(lines, "\n")
end

local function AddRecordEvent(kind, text)
  if not db or not db.raidRecord or not db.raidRecord.active then return end
  local record = db.raidRecord
  record.events = record.events or {}
  local at = record.nativeOnly and (record.elapsedSec or 0) or (Now() - (record.startedAt or Now()))
  record.events[#record.events + 1] = {
    at = at,
    t = math.floor(at),
    kind = kind,
    text = text,
  }
  TrimList(record.events, 200)
end

function CurrentBossSegment(record)
  if not record or not record.currentBossIndex then return nil end
  local boss = record.bossSegments and record.bossSegments[record.currentBossIndex]
  if boss and boss.active then return boss end
  return nil
end

local function NativeProfilerRecentStats(record)
  if type(record.nativeProfilerRecentStats) ~= "table" then
    record.nativeProfilerRecentStats = {
      intervalSec = NATIVE_PROFILER_RECENT_INTERVAL,
      sampleLimitPerBoss = NATIVE_PROFILER_RECENT_SAMPLE_LIMIT,
      attempts = 0,
      successful = 0,
      emptyResults = 0,
      failures = 0,
      unavailable = 0,
      invalidResults = 0,
      metricReadFailures = 0,
      totalCaptureMs = 0,
      maxCaptureMs = 0,
    }
  end
  return record.nativeProfilerRecentStats
end

local function CaptureRecentProfilerSample(record, reason, force, trigger)
  if type(record) ~= "table" or not record.nativeOnly then return false end
  local boss = CurrentBossSegment(record)
  if not boss then return false end
  local atSec = tonumber(record.elapsedSec) or 0
  local isSpike = type(trigger) == "table"
  if isSpike then
    if atSec < (tonumber(boss.nextNativeProfilerSpikeAt) or atSec) then return false end
    boss.nextNativeProfilerSpikeAt = atSec + NATIVE_PROFILER_SPIKE_COOLDOWN
  else
    if not force and atSec < (tonumber(boss.nextNativeProfilerRecentAt) or atSec) then
      return false
    end
    boss.nextNativeProfilerRecentAt = atSec + NATIVE_PROFILER_RECENT_INTERVAL
  end

  local stats = NativeProfilerRecentStats(record)
  stats.attempts = (stats.attempts or 0) + 1
  boss.nativeProfilerRecentAttemptCount = (boss.nativeProfilerRecentAttemptCount or 0) + 1
  local started = type(debugprofilestop) == "function" and debugprofilestop() or nil
  local capability = record.nativeProfilerCapability
  local rows, status
  if type(capability) == "table" and capability.topK then
    rows, status = CopyProfilerTopK("RecentAverageTime", NATIVE_PROFILER_TOP_K)
  else
    status = "unavailable"
  end
  status = status or "invalid"
  local applicationMs = ReadProfilerMetric("GetApplicationMetric", "RecentAverageTime")
  local addonMs = ReadProfilerMetric("GetOverallMetric", "RecentAverageTime")
  local metricReadFailed = not IsFiniteNumber(applicationMs) or not IsFiniteNumber(addonMs)
  local captureMs = 0
  if started ~= nil and type(debugprofilestop) == "function" then
    captureMs = math.max(0, debugprofilestop() - started)
  end

  if status == "ok" then
    stats.successful = (stats.successful or 0) + 1
    boss.nativeProfilerRecentSuccessful = (boss.nativeProfilerRecentSuccessful or 0) + 1
  elseif status == "empty" then
    stats.emptyResults = (stats.emptyResults or 0) + 1
    boss.nativeProfilerRecentEmptyResults = (boss.nativeProfilerRecentEmptyResults or 0) + 1
  elseif status == "unavailable" then
    stats.unavailable = (stats.unavailable or 0) + 1
    boss.nativeProfilerRecentUnavailable = (boss.nativeProfilerRecentUnavailable or 0) + 1
  else
    stats.failures = (stats.failures or 0) + 1
    boss.nativeProfilerRecentFailures = (boss.nativeProfilerRecentFailures or 0) + 1
    if status == "invalid" then
      stats.invalidResults = (stats.invalidResults or 0) + 1
    end
  end
  if metricReadFailed and status ~= "unavailable" then
    stats.metricReadFailures = (stats.metricReadFailures or 0) + 1
    boss.nativeProfilerRecentMetricReadFailures = (boss.nativeProfilerRecentMetricReadFailures or 0) + 1
  end
  stats.totalCaptureMs = (stats.totalCaptureMs or 0) + captureMs
  stats.maxCaptureMs = math.max(stats.maxCaptureMs or 0, captureMs)
  boss.nativeProfilerRecentTotalCaptureMs = (boss.nativeProfilerRecentTotalCaptureMs or 0) + captureMs
  boss.nativeProfilerRecentMaxCaptureMs = math.max(boss.nativeProfilerRecentMaxCaptureMs or 0, captureMs)

  local sample = {
    atSec = atSec,
    bossElapsedSec = math.max(0, atSec - (tonumber(boss.startAt) or atSec)),
    reason = tostring(reason or "interval"),
    status = status,
    topAddOns = rows or {},
    applicationRecentMs = applicationMs,
    addonRecentMs = addonMs,
    captureMs = captureMs,
  }
  if isSpike then
    sample.triggerFps = tonumber(trigger.fps) or 0
    sample.triggerWorstFrameMs = tonumber(trigger.worstFrameMs) or 0
    sample.triggerOver50Frames = tonumber(trigger.over50Frames) or 0
    sample.triggerOver100Frames = tonumber(trigger.over100Frames) or 0
  end
  if IsFiniteNumber(applicationMs) and IsFiniteNumber(addonMs) then
    sample.nonAddonRecentMs = math.max(0, applicationMs - addonMs)
    if applicationMs > 0 then
      sample.addonShare = math.max(0, math.min(1, addonMs / applicationMs))
    end
  end
  boss.nativeProfilerRecentSamples = boss.nativeProfilerRecentSamples or {}
  if #boss.nativeProfilerRecentSamples >= NATIVE_PROFILER_RECENT_SAMPLE_LIMIT then
    table.remove(boss.nativeProfilerRecentSamples, 1)
    boss.nativeProfilerRecentSamplesDropped = (boss.nativeProfilerRecentSamplesDropped or 0) + 1
  end
  boss.nativeProfilerRecentSamples[#boss.nativeProfilerRecentSamples + 1] = sample
  return true
end

-- A scene run is deliberately manual.  It uses the same sparse native
-- profiler read as the Boss black-box, but does not start combat logging or
-- inspect any other addon's frames/events.  This keeps a Dalaran/main-city
-- sample representative instead of turning the diagnostic into the problem.
function RaidOptimizerSceneIdentity()
  local inInstance, instanceType = false, "none"
  if type(IsInInstance) == "function" then
    local ok, inside, kind = pcall(IsInInstance)
    if ok then inInstance, instanceType = inside and true or false, tostring(kind or "none") end
  end
  local zone = "未知区域"
  if type(GetRealZoneText) == "function" then zone = NonEmptyText(SafeCall(GetRealZoneText), zone) end
  if zone == "未知区域" and type(GetZoneText) == "function" then zone = NonEmptyText(SafeCall(GetZoneText), zone) end
  if inInstance then
    return "instance:" .. instanceType .. ":" .. zone, "副本", zone
  end
  local resting = type(IsResting) == "function" and SafeCall(IsResting) and true or false
  return (resting and "city:" or "world:") .. zone, (resting and "主城/休息区" or "野外/开放世界"), zone
end

function RaidOptimizerUpdateSceneSegment(record, sample)
  if type(record) ~= "table" or not record.sceneMode or type(sample) ~= "table" then return nil end
  local identity, kind, zone = RaidOptimizerSceneIdentity()
  local scene = record.currentSceneSegment
  if not scene or scene.identity ~= identity then
    if scene then scene.endAt = sample.at or record.elapsedSec or scene.startAt end
    record.sceneSegments = record.sceneSegments or {}
    scene = {
      identity = identity,
      kind = kind,
      zone = zone,
      startAt = sample.at or record.elapsedSec or 0,
      sampleCount = 0,
      fpsSum = 0,
      minFPS = 9999,
      maxFrameMs = 0,
      over50Frames = 0,
      nativeProfilerRecentSamples = {},
    }
    record.sceneSegments[#record.sceneSegments + 1] = scene
    record.currentSceneSegment = scene
  end
  scene.sampleCount = (scene.sampleCount or 0) + 1
  if sample.fpsValid and (sample.fps or 0) > 0 then
    scene.fpsSum = (scene.fpsSum or 0) + sample.fps
    scene.minFPS = math.min(scene.minFPS or sample.fps, sample.fps)
  end
  scene.maxFrameMs = math.max(scene.maxFrameMs or 0, sample.worstFrameMs or 0)
  scene.over50Frames = (scene.over50Frames or 0) + (sample.over50Frames or 0)
  sample.sceneKind, sample.sceneZone = scene.kind, scene.zone
  return scene
end

function RaidOptimizerCaptureSceneProfilerSample(record, scene, reason, force, trigger)
  if type(record) ~= "table" or not record.sceneMode or type(scene) ~= "table" then return false end
  local atSec = tonumber(record.elapsedSec) or 0
  local isSpike = type(trigger) == "table"
  if isSpike then
    if atSec < (tonumber(scene.nextProfilerSpikeAt) or atSec) then return false end
    scene.nextProfilerSpikeAt = atSec + NATIVE_PROFILER_SPIKE_COOLDOWN
  else
    if not force and atSec < (tonumber(scene.nextProfilerAt) or atSec) then return false end
    scene.nextProfilerAt = atSec + NATIVE_PROFILER_RECENT_INTERVAL
  end
  local started = type(debugprofilestop) == "function" and debugprofilestop() or nil
  local rows, status = CopyProfilerTopK("RecentAverageTime", NATIVE_PROFILER_TOP_K)
  local applicationMs = ReadProfilerMetric("GetApplicationMetric", "RecentAverageTime")
  local addonMs = ReadProfilerMetric("GetOverallMetric", "RecentAverageTime")
  local sample = {
    atSec = atSec, reason = tostring(reason or "interval"), status = status or "invalid",
    topAddOns = rows or {}, applicationRecentMs = applicationMs, addonRecentMs = addonMs,
    captureMs = started and math.max(0, debugprofilestop() - started) or 0,
  }
  if isSpike then sample.triggerFps, sample.triggerWorstFrameMs = trigger.fps or 0, trigger.worstFrameMs or 0 end
  if IsFiniteNumber(applicationMs) and IsFiniteNumber(addonMs) then
    sample.nonAddonRecentMs = math.max(0, applicationMs - addonMs)
    if applicationMs > 0 then sample.addonShare = math.max(0, math.min(1, addonMs / applicationMs)) end
  end
  if #scene.nativeProfilerRecentSamples >= 720 then
    table.remove(scene.nativeProfilerRecentSamples, 1)
    scene.nativeProfilerRecentSamplesDropped = (scene.nativeProfilerRecentSamplesDropped or 0) + 1
  end
  scene.nativeProfilerRecentSamples[#scene.nativeProfilerRecentSamples + 1] = sample
  return true
end

local function StartBossSegment(record, encounterId, encounterName, difficultyId, groupSize)
  if not record then return end
  record.bossSegments = record.bossSegments or {}
  local startAt = record.nativeOnly and (record.elapsedSec or 0) or (Now() - (record.startedAt or Now()))
  local boss = {
    active = true,
    encounterId = encounterId or 0,
    name = encounterName or "未知Boss",
    difficultyId = tonumber(difficultyId) or 0,
    groupSize = tonumber(groupSize) or 0,
    startAt = startAt,
    startT = math.floor(startAt),
    endT = nil,
    sampleCount = 0,
    fpsSum = 0,
    totalFrames = 0,
    totalFrameMs = 0,
    minFPS = 9999,
    maxCombatLogs = 0,
    maxLuaErrors = 0,
    maxLgfQueries = 0,
    maxWAEvents = 0,
    maxPlates = 0,
    maxFrameMs = 0,
    over50Frames = 0,
    over100Frames = 0,
    maxCombatLogsPerSecond = 0,
    logCategories = {},
    logScopes = {},
    spellTotals = {},
    lowSamples = {},
  }
  boss.startClockAnchor = CaptureClockAnchor(record, "encounter_start:" .. tostring(boss.encounterId))
  boss.candidateIdentityStart = CaptureCandidateIdentity and CaptureCandidateIdentity() or nil
  local hubStart = GetHubFlightCounters and GetHubFlightCounters() or nil
  if hubStart and CopyHubFlightCounters then
    boss.hubFlightStart = CopyHubFlightCounters(hubStart)
  end
  if record.nativeOnly then
    boss.nativeProfilerStart = CaptureNativeProfilerSnapshot(record, "start")
  end
  record.bossSegments[#record.bossSegments + 1] = boss
  record.currentBossIndex = #record.bossSegments
  if record.nativeOnly then
    CaptureRecentProfilerSample(record, "boss_start", true)
  end
end

local function FinishBossSegment(record, success)
  local boss = CurrentBossSegment(record)
  if not boss then return end
  if record.nativeOnly then
    CaptureRecentProfilerSample(record, "boss_end", true)
  end
  boss.active = false
  boss.success = success == 1
  boss.endAt = record.nativeOnly and (record.elapsedSec or boss.startAt or 0) or (Now() - (record.startedAt or Now()))
  boss.endT = math.floor(boss.endAt)
  boss.endClockAnchor = CaptureClockAnchor(record, "encounter_end:" .. tostring(boss.encounterId))
  boss.candidateIdentityEnd = CaptureCandidateIdentity and CaptureCandidateIdentity() or nil
  local hubEnd = GetHubFlightCounters and GetHubFlightCounters() or nil
  if hubEnd and CopyHubFlightCounters then
    boss.hubFlightEnd = CopyHubFlightCounters(hubEnd)
    if BuildHubFlightDelta then
      boss.candidateRouteDelta = BuildHubFlightDelta(boss.hubFlightStart, boss.hubFlightEnd)
    end
  end
  boss.candidateTelemetryAvailable = type(boss.candidateRouteDelta) == "table"
    and type(boss.candidateIdentityEnd) == "table"
  record.currentBossIndex = nil
  if record.nativeOnly and not boss.nativeProfilerFinalizeScheduled then
    boss.nativeProfilerFinalizeScheduled = true
    local function FinalizeProfiler()
      if boss.nativeProfilerFinalized then return end
      boss.nativeProfilerFinalized = true
      boss.nativeProfilerEnd = CaptureNativeProfilerSnapshot(record, "end")
      FinalizeNativeProfilerBoss(boss)
    end
    if C_Timer and type(C_Timer.After) == "function" then
      C_Timer.After(0, FinalizeProfiler)
    else
      FinalizeProfiler()
    end
  end
end

local function AddBossLowSample(boss, sample)
  if not boss or not sample then return end
  boss.lowSamples = boss.lowSamples or {}
  boss.lowSamples[#boss.lowSamples + 1] = {
    t = sample.t or 0,
    fps = sample.fps or 0,
    combatLogs = sample.combatLogs or 0,
    luaErrors = sample.luaErrors or 0,
    lgfQueries = sample.lgfQueries or 0,
    plates = sample.plates or 0,
    worstFrameMs = sample.worstFrameMs or 0,
    over50Frames = sample.over50Frames or 0,
    over100Frames = sample.over100Frames or 0,
    topSpells = sample.topSpells or "",
  }
  table.sort(boss.lowSamples, function(a, b) return (a.fps or 9999) < (b.fps or 9999) end)
  while #boss.lowSamples > 5 do
    table.remove(boss.lowSamples)
  end
end

local function UpdateBossSnapshot(record, sample)
  local boss = CurrentBossSegment(record)
  if not boss or not sample then return end
  if sample.fpsValid ~= false and (sample.fps or 0) > 0 then
    boss.sampleCount = (boss.sampleCount or 0) + 1
    boss.fpsSum = (boss.fpsSum or 0) + (sample.fps or 0)
    boss.minFPS = math.min(boss.minFPS or sample.fps or 0, sample.fps or 0)
  end
  boss.maxCombatLogs = math.max(boss.maxCombatLogs or 0, sample.combatLogs or 0)
  boss.maxCombatLogsPerSecond = math.max(boss.maxCombatLogsPerSecond or 0, sample.combatLogs or 0)
  boss.maxLuaErrors = math.max(boss.maxLuaErrors or 0, sample.luaErrors or 0)
  boss.maxLgfQueries = math.max(boss.maxLgfQueries or 0, sample.lgfQueries or 0)
  boss.maxWAEvents = math.max(boss.maxWAEvents or 0, sample.waEvents or 0)
  boss.maxPlates = math.max(boss.maxPlates or 0, sample.plates or 0)
  boss.maxFrameMs = math.max(boss.maxFrameMs or 0, sample.worstFrameMs or 0)
  boss.over50Frames = (boss.over50Frames or 0) + (sample.over50Frames or 0)
  boss.over100Frames = (boss.over100Frames or 0) + (sample.over100Frames or 0)
  if (sample.fps or 0) > 0 and ((sample.fps or 0) <= NEARBY_SPELL_FPS_THRESHOLD or (sample.combatLogs or 0) >= 800) then
    AddBossLowSample(boss, sample)
  end
end

local function HandleRecorderCombatLog()
  if not recorderActive or not db or not db.raidRecord or not db.raidRecord.active then return end
  -- Native fallback only counts events. Detailed spell/source attribution is
  -- intentionally deferred to WoWCombatLog on disk so the recorder never
  -- becomes a second combat-log parser during a boss burst.
  combatLogEvents = combatLogEvents + 1
end

local function TopSpellsNear()
  return "详见本地 WoWCombatLog 离线分析"
end

local function AppendNativeSlowFrame(record, frameMs)
  if not record or not record.nativeOnly or frameMs < NATIVE_SLOW_FRAME_MS then return end
  record.nativeSlowFrames = record.nativeSlowFrames or {}
  local row = {
    atSec = record.elapsedSec or 0,
    frameMs = frameMs,
    bossIndex = record.currentBossIndex,
  }
  if record.startedUnixSec then row.estimatedUnixSec = record.startedUnixSec + row.atSec end
  if #record.nativeSlowFrames < NATIVE_SLOW_FRAME_LIMIT then
    record.nativeSlowFrames[#record.nativeSlowFrames + 1] = row
    return
  end
  local index = record.nativeSlowFrameRingNext or 1
  record.nativeSlowFrames[index] = row
  record.nativeSlowFrameRingNext = (index % NATIVE_SLOW_FRAME_LIMIT) + 1
  record.nativeSlowFramesWrapped = true
  record.nativeSlowFramesDropped = (record.nativeSlowFramesDropped or 0) + 1
end

local function AccumulateFrameWindow(record, elapsed)
  if not record then return end
  local ms = math.max(0, (tonumber(elapsed) or 0) * 1000)
  record.elapsedSec = (record.elapsedSec or 0) + math.max(0, tonumber(elapsed) or 0)
  record.totalFrames = (record.totalFrames or 0) + 1
  record.totalFrameMs = (record.totalFrameMs or 0) + ms
  record.frameWindowFrames = (record.frameWindowFrames or 0) + 1
  record.frameWindowMs = (record.frameWindowMs or 0) + ms
  record.frameWindowWorstMs = math.max(record.frameWindowWorstMs or 0, ms)
  if ms >= 33 then record.frameWindowOver33 = (record.frameWindowOver33 or 0) + 1 end
  if ms >= 50 then record.frameWindowOver50 = (record.frameWindowOver50 or 0) + 1 end
  if ms >= 100 then record.frameWindowOver100 = (record.frameWindowOver100 or 0) + 1 end
  local boss = CurrentBossSegment(record)
  if boss then
    boss.totalFrames = (boss.totalFrames or 0) + 1
    boss.totalFrameMs = (boss.totalFrameMs or 0) + ms
  end
  AppendNativeSlowFrame(record, ms)
end

local function AppendSample(record, sample)
  record.samples = record.samples or {}
  if #record.samples < RECORD_MAX_SAMPLES then
    record.samples[#record.samples + 1] = sample
    return
  end
  local index = record.sampleRingNext or 1
  record.samples[index] = sample
  record.sampleRingNext = (index % RECORD_MAX_SAMPLES) + 1
  record.samplesWrapped = true
  record.samplesDropped = (record.samplesDropped or 0) + 1
end

local function OrderedSamples(record)
  local source = (record and record.samples) or {}
  local out = {}
  if not record or not record.samplesWrapped then
    for index = 1, #source do out[#out + 1] = source[index] end
    return out
  end
  local first = record.sampleRingNext or 1
  for offset = 0, #source - 1 do
    local index = ((first + offset - 1) % #source) + 1
    out[#out + 1] = source[index]
  end
  return out
end

local function OrderedNativeSlowFrames(record)
  local source = (record and record.nativeSlowFrames) or {}
  local out = {}
  if not record or not record.nativeSlowFramesWrapped then
    for index = 1, #source do out[#out + 1] = source[index] end
    return out
  end
  local first = record.nativeSlowFrameRingNext or 1
  for offset = 0, #source - 1 do
    local index = ((first + offset - 1) % #source) + 1
    out[#out + 1] = source[index]
  end
  return out
end

local HUB_DELTA_FIELDS = {
  "received", "routeCandidates", "routedFilterSkips", "filtered",
  "dispatched", "callbackErrors", "quarantined", "nativeEventPayloadSkips",
  "compiledDemandChecks", "compiledDemandSkips", "emptyRouteReuses",
  "decodeMs", "gcDrops", "gcDropKB",
  "waBatchCalls", "waBatchGroups", "waBatchDisplays",
  "waBatchRecoveries", "waBatchRecoveryErrors",
  "waCleuVisibilityFastRejects", "waCleuVisibilityDeferrals",
  "waCleuVisibilityPayloadAllocations", "waCleuVisibilityPayloadReuses",
  "waCleuVisibilityEntryAllocations", "waCleuVisibilityEntryReuses",
  "waCleuDispatchEvents", "waCleuNativeDisplays", "waCleuPlannedDisplays",
  "waQueueEvents", "waQueueEntryAllocations", "waQueueEntryReuses",
  "waQueueArgsAllocations", "waQueueReuses",
  "consumerCalls", "consumerWorkMs", "consumerFilterMs",
  "waConsumerCalls", "waConsumerWorkMs", "waConsumerFilterMs",
  "nduiConsumerCalls", "nduiConsumerWorkMs", "nduiConsumerFilterMs",
  "detailsConsumerCalls", "detailsConsumerWorkMs",
  "unitEvents", "unitRefreshes", "auraRefreshes", "fullAuraUpdates",
  "incrementalAuraUpdates", "modernAuraScans", "legacyAuraScans",
  "unitPublishes", "unitRefreshMs", "unitFlushes", "unitBudgetYields",
  "unitDeferredRefreshes", "frameQueries", "frameCacheHits",
  "frameDirectHits", "frameNameplateHits", "frameMisses", "frameScans",
  "frameScanMs", "frameCombatScanSkips",
  "waAuraScans", "waAuraSelectiveScans", "waAuraNativeFallbackScans",
  "waAuraSkippedUnitEvents", "waAuraScannedAuras", "waAuraRelevantAuras",
  "waAuraSkippedAuras", "waAuraStaleMatchesRemoved",
  "waAuraUpdateInfoChecks", "waAuraUpdateInfoSkips",
  "waAuraUpdateInfoFallbacks", "waAuraUpdateInfoAuraLookups",
}

RaidOptimizerStressTestLocalWAReleaseId = "hub-0.5.66-titan-wa-52111-three-file-local-r1"

function RaidOptimizerStressTestGetLocalWACandidateCounters()
  local source = type(RaidOptimizerHubWeakAurasAuraSelectiveStats) == "table"
    and RaidOptimizerHubWeakAurasAuraSelectiveStats or nil
  if not source then return nil end
  return {
    waAuraScans = tonumber(source.scans) or 0,
    waAuraSelectiveScans = tonumber(source.selectiveScans) or 0,
    waAuraNativeFallbackScans = tonumber(source.nativeFallbackScans) or 0,
    waAuraSkippedUnitEvents = tonumber(source.skippedUnitEvents) or 0,
    waAuraScannedAuras = tonumber(source.scannedAuras) or 0,
    waAuraRelevantAuras = tonumber(source.relevantAuras) or 0,
    waAuraSkippedAuras = tonumber(source.skippedAuras) or 0,
    waAuraStaleMatchesRemoved = tonumber(source.staleMatchesRemoved) or 0,
    waAuraUpdateInfoChecks = tonumber(source.updateInfoChecks) or 0,
    waAuraUpdateInfoSkips = tonumber(source.updateInfoSkips) or 0,
    waAuraUpdateInfoFallbacks = tonumber(source.updateInfoFallbacks) or 0,
    waAuraUpdateInfoAuraLookups = tonumber(source.updateInfoAuraLookups) or 0,
  }
end

CopyHubFlightCounters = function(source, target)
  target = target or {}
  for _, key in ipairs(HUB_DELTA_FIELDS) do
    target[key] = tonumber(source and source[key]) or 0
  end
  target.luaMemoryKB = tonumber(source and source.luaMemoryKB) or 0
  target.rate = tonumber(source and source.rate) or 0
  target.waConsumerCount = tonumber(source and source.waConsumerCount) or 0
  target.nduiConsumerCount = tonumber(source and source.nduiConsumerCount) or 0
  return target
end

GetHubFlightCounters = function()
  if RaidOptimizerHub and type(RaidOptimizerHub.GetFlightRecorderStats) == "function" then
    local ok, result = pcall(RaidOptimizerHub.GetFlightRecorderStats)
    if ok and type(result) == "table" then return result end
  end
  return RaidOptimizerStressTestGetLocalWACandidateCounters()
end

BuildHubFlightDelta = function(before, after)
  if type(before) ~= "table" or type(after) ~= "table" then return nil end
  local delta = {}
  for _, key in ipairs(HUB_DELTA_FIELDS) do
    delta[key] = math.max(0, (tonumber(after[key]) or 0) - (tonumber(before[key]) or 0))
  end
  delta.waConsumerCount = tonumber(after.waConsumerCount) or 0
  delta.nduiConsumerCount = tonumber(after.nduiConsumerCount) or 0
  delta.receivedPerDelivered = (delta.dispatched or 0) > 0
    and ((delta.received or 0) / delta.dispatched) or 0
  return delta
end

CaptureCandidateIdentity = function()
  local config = type(RaidOptimizerHubRuntimeConfig) == "table"
    and RaidOptimizerHubRuntimeConfig or {}
  local identity = {
    hubAvailable = type(RaidOptimizerHub) == "table",
    hubVersion = "",
    mode = "unavailable",
    releaseId = tostring(config.releaseId or ""),
    runtimeGrantExpiresAt = tonumber(config.expiresAt) or 0,
  }
  if type(RaidOptimizerHub) == "table" then
    if type(RaidOptimizerHub.GetVersion) == "function" then
      local ok, value = pcall(RaidOptimizerHub.GetVersion)
      if ok then identity.hubVersion = tostring(value or "") end
    end
    if type(RaidOptimizerHub.GetMode) == "function" then
      local ok, value = pcall(RaidOptimizerHub.GetMode)
      if ok then identity.mode = tostring(value or "unknown") end
    end
  elseif RaidOptimizerStressTestGetLocalWACandidateCounters() then
    identity.mode = "wa-52111-local"
    identity.releaseId = RaidOptimizerStressTestLocalWAReleaseId
  end
  return identity
end

local function CaptureHubFlightDelta(record)
  local current = GetHubFlightCounters()
  if not current then return nil end
  local previous = record.lastHubFlight or {}
  local delta = {}
  for _, key in ipairs(HUB_DELTA_FIELDS) do
    delta[key] = math.max(0, (tonumber(current[key]) or 0) - (tonumber(previous[key]) or 0))
  end
  delta.rate = tonumber(current.rate) or 0
  delta.luaMemoryKB = tonumber(current.luaMemoryKB) or 0
  delta.luaGrowthKB = delta.luaMemoryKB - (tonumber(previous.luaMemoryKB) or delta.luaMemoryKB)
  -- Hub timers are sampled estimates. UnitData refreshMs surrounds Publish(),
  -- so it can already include subscriber callback work. Keep a conservative
  -- non-overlapping estimate for comparisons and retain the simple sum only as
  -- an explicitly labelled upper estimate; never present either as raw time.
  local consumerEstimate = (delta.consumerWorkMs or 0) + (delta.consumerFilterMs or 0)
  local providerEstimate = delta.unitRefreshMs or 0
  delta.timingEstimated = true
  delta.consumerInclusiveEstimateMs = consumerEstimate
  delta.providerInclusiveEstimateMs = providerEstimate
  delta.measuredMs = (delta.decodeMs or 0) + (delta.frameScanMs or 0)
    + math.max(consumerEstimate, providerEstimate)
  delta.upperEstimateMs = (delta.decodeMs or 0) + (delta.frameScanMs or 0)
    + consumerEstimate + providerEstimate
  record.lastHubFlight = CopyHubFlightCounters(current, record.lastHubFlight)
  return delta
end

local function InstallDetailsHooks()
  if detailsHooksInstalled or type(hooksecurefunc) ~= "function" or type(Details) ~= "table" then
    return detailsHooksInstalled
  end
  local installed = 0
  if type(Details.RefreshMainWindow) == "function" then
    local ok = pcall(hooksecurefunc, Details, "RefreshMainWindow", function()
      if recorderActive then detailsRefreshCalls = detailsRefreshCalls + 1 end
    end)
    if ok then installed = installed + 1 end
  end
  if type(Details.InstanceRefreshRows) == "function" then
    local ok = pcall(hooksecurefunc, Details, "InstanceRefreshRows", function()
      if recorderActive then detailsRowRefreshCalls = detailsRowRefreshCalls + 1 end
    end)
    if ok then installed = installed + 1 end
  end
  detailsHooksInstalled = installed > 0
  return detailsHooksInstalled
end

local function StopRobotProbe()
  local frameObject = robotProbeFrame
  if frameObject and robotProbeWrapper and type(frameObject.GetScript) == "function"
    and type(frameObject.SetScript) == "function"
  then
    local ok, current = pcall(frameObject.GetScript, frameObject, "OnUpdate")
    if ok and current == robotProbeWrapper then
      pcall(frameObject.SetScript, frameObject, "OnUpdate", robotProbeOriginal)
    end
  end
  robotProbeFrame = nil
  robotProbeOriginal = nil
  robotProbeWrapper = nil
  robotProbeEndsAt = 0
end

local function InstallRobotProbe(now)
  now = now or Now()
  if robotProbeWrapper then return true end
  if now - (robotProbeLastTry or 0) < 0.8 then return false end
  robotProbeLastTry = now
  local target = _G["WOW-Robot"]
  if not target or type(target.GetScript) ~= "function" or type(target.SetScript) ~= "function"
    or type(debugprofilestop) ~= "function"
  then
    return false
  end
  if type(target.IsProtected) == "function" then
    local ok, protected = pcall(target.IsProtected, target)
    if not ok or protected then return false end
  end
  local ok, original = pcall(target.GetScript, target, "OnUpdate")
  if not ok or type(original) ~= "function" then return false end
  local wrapper
  wrapper = function(self, elapsed, ...)
    local started = debugprofilestop()
    original(self, elapsed, ...)
    local spent = math.max(0, debugprofilestop() - started)
    robotProbeCalls = robotProbeCalls + 1
    robotProbeMs = robotProbeMs + spent
    if spent > robotProbeMaxMs then robotProbeMaxMs = spent end
  end
  local setOk = pcall(target.SetScript, target, "OnUpdate", wrapper)
  if not setOk then return false end
  robotProbeFrame = target
  robotProbeOriginal = original
  robotProbeWrapper = wrapper
  return true
end

local function ArmAttributionWindow(record, reason, duration, force)
  if not recorderActive or not record then return false end
  local now = Now()
  if not force and now < (record.nextAttributionWindowAt or 0) then return false end
  record.nextAttributionWindowAt = now + ROBOT_PROBE_COOLDOWN
  robotProbeEndsAt = math.max(robotProbeEndsAt or 0, now + (duration or ROBOT_PROBE_SECONDS))
  record.attributionWindows = (record.attributionWindows or 0) + 1
  record.attributionReasons = record.attributionReasons or {}
  record.attributionReasons[#record.attributionReasons + 1] = {
    t = math.floor(now - (record.startedAt or now)),
    reason = tostring(reason or "sample"),
  }
  TrimList(record.attributionReasons, 32)
  return InstallRobotProbe(now)
end

local function MaintainAttributionWindow(record, now)
  if robotProbeWrapper then
    if now >= (robotProbeEndsAt or 0) then
      StopRobotProbe()
      return
    end
    if robotProbeFrame and type(robotProbeFrame.GetScript) == "function" then
      local ok, current = pcall(robotProbeFrame.GetScript, robotProbeFrame, "OnUpdate")
      if not ok or current ~= robotProbeWrapper then
        robotProbeFrame, robotProbeOriginal, robotProbeWrapper = nil, nil, nil
      end
    end
  elseif now < (robotProbeEndsAt or 0) then
    InstallRobotProbe(now)
  end
  local boss = CurrentBossSegment(record)
  if boss and now >= (record.nextPeriodicAttributionAt or 0) then
    record.nextPeriodicAttributionAt = now + 30
    ArmAttributionWindow(record, "boss_periodic", ROBOT_PROBE_SECONDS, true)
  end
end

local function SampleCpuBound(record)
  local state = "unknown"
  if type(IsCpuBound) == "function" then
    local ok, value = pcall(IsCpuBound)
    if ok and type(value) == "boolean" then
      state = value and "cpu" or "not-cpu"
    elseif not ok then
      record.cpuBoundErrors = (record.cpuBoundErrors or 0) + 1
    end
  end
  record.cpuBoundSamples = record.cpuBoundSamples or { cpu = 0, notCpu = 0, unknown = 0 }
  if state == "cpu" then
    record.cpuBoundSamples.cpu = record.cpuBoundSamples.cpu + 1
  elseif state == "not-cpu" then
    record.cpuBoundSamples.notCpu = record.cpuBoundSamples.notCpu + 1
  else
    record.cpuBoundSamples.unknown = record.cpuBoundSamples.unknown + 1
  end
  local boss = CurrentBossSegment(record)
  if boss then
    boss.cpuBoundSamples = boss.cpuBoundSamples or { cpu = 0, notCpu = 0, unknown = 0 }
    if state == "cpu" then
      boss.cpuBoundSamples.cpu = boss.cpuBoundSamples.cpu + 1
    elseif state == "not-cpu" then
      boss.cpuBoundSamples.notCpu = boss.cpuBoundSamples.notCpu + 1
    else
      boss.cpuBoundSamples.unknown = boss.cpuBoundSamples.unknown + 1
    end
  end
  return state
end

local function RecordNativeSnapshot(record, elapsed, frameCount, frameWindowMs, fpsValid, fps, worstFrameMs, over33Frames, over50Frames, over100Frames, reason, recorderStarted)
  local atSec = record.elapsedSec or elapsed or 0
  local sample = {
    seq = (record.sampleCount or 0) + 1,
    -- Persist the encounter ownership on every sparse sample.  A later
    -- non-boss reload must not make it impossible to keep the real Boss
    -- timeline for offline diagnosis.
    bossIndex = record.currentBossIndex,
    at = atSec,
    atPrecise = atSec,
    t = math.floor(atSec),
    fps = fps,
    fpsValid = fpsValid,
    fpsWindowMs = frameWindowMs,
    fpsWindowStartAt = math.max(0, atSec - frameWindowMs / 1000),
    fpsWindowEndAt = atSec,
    fpsFrameCount = frameCount,
    worstFrameMs = worstFrameMs,
    over33Frames = over33Frames,
    over50Frames = over50Frames,
    over100Frames = over100Frames,
    cpuBound = SampleCpuBound(record),
    combatLogs = 0,
    luaErrors = 0,
    lgfQueries = 0,
    plates = 0,
    nativeOnly = true,
    reason = reason or "",
  }
  if record.startedUnixSec then sample.estimatedUnixSec = record.startedUnixSec + atSec end
  RaidOptimizerUpdateSceneSegment(record, sample)
  AppendSample(record, sample)
  UpdateBossSnapshot(record, sample)
  record.sampleCount = (record.sampleCount or 0) + 1
  if fpsValid then
    record.fpsSampleCount = (record.fpsSampleCount or 0) + 1
    record.minFPS = math.min(record.minFPS or fps, fps)
    record.fpsSum = (record.fpsSum or 0) + fps
    record.lastFPS = fps
  end
  record.maxFrameMs = math.max(record.maxFrameMs or 0, worstFrameMs)
  record.over33Frames = (record.over33Frames or 0) + over33Frames
  record.over50Frames = (record.over50Frames or 0) + over50Frames
  record.over100Frames = (record.over100Frames or 0) + over100Frames
  record.updatedAtText = date("%Y-%m-%d %H:%M:%S")
  record.lastSnapshotAt = atSec
  if recorderStarted and type(debugprofilestop) == "function" then
    sample.recorderMs = math.max(0, debugprofilestop() - recorderStarted)
    record.recorderSampleMs = (record.recorderSampleMs or 0) + sample.recorderMs
    record.maxRecorderSampleMs = math.max(record.maxRecorderSampleMs or 0, sample.recorderMs)
  end
  return sample
end

local function RecordSnapshot(reason)
  if not db or not db.raidRecord or not db.raidRecord.active then return end
  local recorderStarted = type(debugprofilestop) == "function" and debugprofilestop() or 0
  local record = db.raidRecord
  local now = Now()
  local elapsed = now - (record.startedAt or now)
  local frameCount = record.frameWindowFrames or 0
  local frameWindowMs = record.frameWindowMs or 0
  local fpsValid = frameCount > 0 and frameWindowMs >= MIN_FPS_WINDOW_MS
  local fps = fpsValid and (frameCount * 1000 / frameWindowMs) or 0
  local worstFrameMs = record.frameWindowWorstMs or 0
  local over33Frames = record.frameWindowOver33 or 0
  local over50Frames = record.frameWindowOver50 or 0
  local over100Frames = record.frameWindowOver100 or 0
  record.frameWindowFrames = 0
  record.frameWindowMs = 0
  record.frameWindowWorstMs = 0
  record.frameWindowOver33 = 0
  record.frameWindowOver50 = 0
  record.frameWindowOver100 = 0
  if record.nativeOnly then
    return RecordNativeSnapshot(
      record,
      elapsed,
      frameCount,
      frameWindowMs,
      fpsValid,
      fps,
      worstFrameMs,
      over33Frames,
      over50Frames,
      over100Frames,
      reason,
      recorderStarted > 0 and recorderStarted or nil
    )
  end
  local cpuTotal, cpuRows = 0, {}
  if record.deepCpu and CpuProfileEnabled() then
    cpuTotal, cpuRows = GetAddonCPU()
  end
  local lgf = GetLibGetFrameStats()
  local cpuDelta = cpuTotal - (record.lastCpuTotal or cpuTotal)
  local waDelta = record.deepEvents and (waScanEvents - (record.lastWAEvents or waScanEvents)) or 0
  local logDelta = combatLogEvents - (record.lastCombatLogEvents or combatLogEvents)
  local luaErrorDelta = (record.luaErrorCount or 0) - (record.lastLuaErrorCount or (record.luaErrorCount or 0))
  local lgfQueries = lgf and lgf.queries or 0
  local lgfDelta = lgfQueries - (record.lastLgfQueries or lgfQueries)
  local hubFlight = CaptureHubFlightDelta(record)
  local detailRefreshDelta = detailsRefreshCalls - (record.lastDetailsRefreshCalls or detailsRefreshCalls)
  local detailRowsDelta = detailsRowRefreshCalls - (record.lastDetailsRowRefreshCalls or detailsRowRefreshCalls)
  local robotCallsDelta = robotProbeCalls - (record.lastRobotProbeCalls or robotProbeCalls)
  local robotMsDelta = robotProbeMs - (record.lastRobotProbeMs or robotProbeMs)
  local robotMaxMs = robotProbeMaxMs or 0
  robotProbeMaxMs = 0
  local nameplateAddDelta = nameplateAdds - (record.lastNameplateAdds or nameplateAdds)
  local nameplateRemoveDelta = nameplateRemoves - (record.lastNameplateRemoves or nameplateRemoves)
  local _, _, homeLatency, worldLatency = SafeCall(GetNetStats)
  local logCategoryRows, logScopeRows
  if record.captureDetailedCombat then
    logCategoryRows = DeltaCounter(record.logCategories or {}, record.lastLogCategories or {})
    logScopeRows = DeltaCounter(record.logScopes or {}, record.lastLogScopes or {})
  end
  local topCpu = record.deepCpu and CopyTopCpuDelta(cpuRows, record.lastCpuRows or cpuRows, 8) or nil
  if record.deepCpu and CpuProfileEnabled() then
    AddCpuAggregate(record, cpuRows, record.lastCpuRows or cpuRows)
  end

  record.samples = record.samples or {}
  local sample = {
    seq = (record.sampleCount or 0) + 1,
    bossIndex = record.currentBossIndex,
    at = elapsed,
    t = math.floor(elapsed),
    fps = fps,
    fpsValid = fpsValid,
    fpsWindowMs = frameWindowMs,
    fpsFrameCount = frameCount,
    plates = CountVisibleNameplates(),
    cpuDelta = cpuDelta,
    waEvents = waDelta,
    combatLogs = logDelta,
    luaErrors = luaErrorDelta,
    lgfQueries = lgfDelta,
    logCategories = logCategoryRows,
    logScopes = logScopeRows,
    topCpu = topCpu,
    worstFrameMs = worstFrameMs,
    over33Frames = over33Frames,
    over50Frames = over50Frames,
    over100Frames = over100Frames,
    luaMemoryKB = SafeCall(collectgarbage, "count") or 0,
    luaGrowthKB = hubFlight and hubFlight.luaGrowthKB or 0,
    gcDrops = hubFlight and hubFlight.gcDrops or 0,
    gcDropKB = hubFlight and hubFlight.gcDropKB or 0,
    hub = hubFlight,
    hubMeasuredMs = hubFlight and hubFlight.measuredMs or 0,
    hubEstimatedMs = hubFlight and hubFlight.measuredMs or 0,
    hubEstimatedUpperMs = hubFlight and hubFlight.upperEstimateMs or 0,
    hubTimingEstimated = hubFlight and hubFlight.timingEstimated and true or false,
    waConsumerMs = hubFlight and hubFlight.waConsumerWorkMs or 0,
    auraRefreshMs = hubFlight and hubFlight.unitRefreshMs or 0,
    auraRefreshEstimatedMs = hubFlight and hubFlight.unitRefreshMs or 0,
    detailsRefreshes = detailRefreshDelta,
    detailsRowRefreshes = detailRowsDelta,
    robotCalls = robotCallsDelta,
    robotMs = robotMsDelta,
    robotMaxMs = robotMaxMs,
    nameplateAdds = nameplateAddDelta,
    nameplateRemoves = nameplateRemoveDelta,
    homeLatency = tonumber(homeLatency) or 0,
    worldLatency = tonumber(worldLatency) or 0,
    reason = reason or "",
  }
  local isLowFps = fpsValid and fps > 0 and fps <= LOW_FPS_THRESHOLD
  local needsNearbySpells = fpsValid and fps > 0 and fps <= NEARBY_SPELL_FPS_THRESHOLD
  local isDrop = fpsValid and record.lastFPS and record.lastFPS > 0 and fps > 0 and fps < record.lastFPS * 0.68
  if needsNearbySpells or isDrop then
    sample.topSpells = TopSpellsNear(record, sample.t, LOW_FPS_SPELL_WINDOW, 4)
  end
  AppendSample(record, sample)
  UpdateBossSnapshot(record, sample)

  record.sampleCount = (record.sampleCount or 0) + 1
  if fpsValid then
    record.fpsSampleCount = (record.fpsSampleCount or 0) + 1
    record.minFPS = math.min(record.minFPS or fps, fps)
    record.fpsSum = (record.fpsSum or 0) + fps
  end
  record.maxCpuDelta = math.max(record.maxCpuDelta or 0, cpuDelta)
  record.maxWAEvents = math.max(record.maxWAEvents or 0, waDelta)
  record.maxCombatLogs = math.max(record.maxCombatLogs or 0, logDelta)
  record.maxCombatLogsPerSecond = math.max(record.maxCombatLogsPerSecond or 0, logDelta)
  record.maxLuaErrors = math.max(record.maxLuaErrors or 0, luaErrorDelta)
  record.maxLgfQueries = math.max(record.maxLgfQueries or 0, lgfDelta)
  record.maxFrameMs = math.max(record.maxFrameMs or 0, worstFrameMs)
  record.over33Frames = (record.over33Frames or 0) + over33Frames
  record.over50Frames = (record.over50Frames or 0) + over50Frames
  record.over100Frames = (record.over100Frames or 0) + over100Frames
  record.maxHubMeasuredMs = math.max(record.maxHubMeasuredMs or 0, sample.hubMeasuredMs or 0)
  record.maxHubEstimatedMs = math.max(record.maxHubEstimatedMs or 0, sample.hubEstimatedMs or 0)
  record.maxHubEstimatedUpperMs = math.max(record.maxHubEstimatedUpperMs or 0, sample.hubEstimatedUpperMs or 0)
  record.maxWAConsumerMs = math.max(record.maxWAConsumerMs or 0, sample.waConsumerMs or 0)
  record.maxAuraRefreshMs = math.max(record.maxAuraRefreshMs or 0, sample.auraRefreshMs or 0)
  record.maxRobotMs = math.max(record.maxRobotMs or 0, robotMsDelta)
  record.maxRobotCallMs = math.max(record.maxRobotCallMs or 0, robotMaxMs)
  record.maxDetailsRefreshes = math.max(record.maxDetailsRefreshes or 0, detailRefreshDelta)
  record.maxDetailsRowRefreshes = math.max(record.maxDetailsRowRefreshes or 0, detailRowsDelta)
  record.maxLuaGrowthKB = math.max(record.maxLuaGrowthKB or 0, sample.luaGrowthKB or 0)
  record.totalGcDrops = (record.totalGcDrops or 0) + (sample.gcDrops or 0)
  if worstFrameMs >= 50 or isDrop then
    ArmAttributionWindow(record, worstFrameMs >= 50 and "slow_frame" or "fps_drop", ROBOT_PROBE_SECONDS, false)
  end
  if isLowFps then
    AddRecordEvent("掉帧", string.format("FPS %.1f，LGF查询 %d，Lua错误 %d，附近事件：%s", fps, lgfDelta, luaErrorDelta, sample.topSpells or "无"))
  elseif isDrop then
    AddRecordEvent("骤降", string.format("FPS %.1f -> %.1f，LGF查询 %d，Lua错误 %d，附近事件：%s", record.lastFPS, fps, lgfDelta, luaErrorDelta, sample.topSpells or "无"))
  end

  if fpsValid then record.lastFPS = fps end
  record.lastCpuTotal = cpuTotal
  record.lastCpuRows = cpuRows
  record.lastWAEvents = waScanEvents
  record.lastCombatLogEvents = combatLogEvents
  record.lastLuaErrorCount = record.luaErrorCount or 0
  record.lastLgfQueries = lgfQueries
  record.lastDetailsRefreshCalls = detailsRefreshCalls
  record.lastDetailsRowRefreshCalls = detailsRowRefreshCalls
  record.lastRobotProbeCalls = robotProbeCalls
  record.lastRobotProbeMs = robotProbeMs
  record.lastNameplateAdds = nameplateAdds
  record.lastNameplateRemoves = nameplateRemoves
  if record.captureDetailedCombat then
    record.lastLogCategories = CopyCounter(record.logCategories or {})
    record.lastLogScopes = CopyCounter(record.logScopes or {})
  end
  record.updatedAtText = date("%Y-%m-%d %H:%M:%S")
  record.lastSnapshotAt = now
  if recorderStarted > 0 and type(debugprofilestop) == "function" then
    sample.recorderMs = math.max(0, debugprofilestop() - recorderStarted)
    record.recorderSampleMs = (record.recorderSampleMs or 0) + sample.recorderMs
    record.maxRecorderSampleMs = math.max(record.maxRecorderSampleMs or 0, sample.recorderMs)
  end
  return sample
end

local function FormatNativeTopAddOns(rows, limit)
  local out = {}
  for index = 1, math.min(limit or 5, #(rows or {})) do
    local row = rows[index]
    out[#out + 1] = string.format("%s %.2fms", tostring(row.addOnName or "未知插件"), tonumber(row.metricValue) or 0)
  end
  return #out > 0 and table.concat(out, "；") or "原生接口未返回排行"
end

local function FormatWeakAurasProfile(profile, fullReport)
  if type(profile) ~= "table" then return "未开启 WA 深度归因" end
  local limit = fullReport and 10 or 4
  local function FormatRows(rows)
    local out = {}
    for index = 1, math.min(limit, #(rows or {})) do
      local row = rows[index]
      out[#out + 1] = string.format(
        "%d.%s 累计 %.2fms，尖峰 %.2fms，次数 %d",
        index, tostring(row.name or "未知"), tonumber(row.elapsed) or 0,
        tonumber(row.spike) or 0, tonumber(row.count) or 0)
    end
    return #out > 0 and table.concat(out, "；") or "无有效样本"
  end
  return string.format(
    "WA窗口 %.2fms，WA内部 %.2fms；系统：%s；Aura：%s",
    tonumber(profile.elapsed) or 0, tonumber(profile.waElapsed) or 0,
    FormatRows(profile.systems), FormatRows(profile.auras))
end

function RaidOptimizerFormatScenePluginRanking(scene, limit)
  local totals = {}
  for _, snapshot in ipairs(scene.nativeProfilerRecentSamples or {}) do
    if snapshot.status == "ok" then
      for _, row in ipairs(snapshot.topAddOns or {}) do
        local name = tostring(row.addOnName or "未知插件")
        local value = tonumber(row.metricValue) or 0
        local bucket = totals[name] or { total = 0, hits = 0, peak = 0, slowHits = 0 }
        bucket.total = bucket.total + value
        bucket.hits = bucket.hits + 1
        bucket.peak = math.max(bucket.peak, value)
        if snapshot.reason == "slow_frame" then bucket.slowHits = bucket.slowHits + 1 end
        totals[name] = bucket
      end
    end
  end
  local rows = {}
  for name, bucket in pairs(totals) do
    rows[#rows + 1] = { name = name, average = bucket.total / math.max(1, bucket.hits), peak = bucket.peak, slowHits = bucket.slowHits }
  end
  table.sort(rows, function(a, b)
    if a.slowHits ~= b.slowHits then return a.slowHits > b.slowHits end
    return a.average > b.average
  end)
  local lines = {}
  for index = 1, math.min(limit or 8, #rows) do
    local row = rows[index]
    lines[#lines + 1] = string.format("%d.%s 平均 %.2fms，峰值 %.2fms，慢帧命中 %d", index, row.name, row.average, row.peak, row.slowHits)
  end
  return #lines > 0 and table.concat(lines, "；") or "未拿到原生插件耗时排行"
end

function RaidOptimizerSummarizeSceneSegments(record)
  local lines = {}
  for _, scene in ipairs(record.sceneSegments or {}) do
    local duration = math.max(0, (scene.endAt or record.elapsedSec or scene.startAt or 0) - (scene.startAt or 0))
    local avg = (scene.sampleCount or 0) > 0 and (scene.fpsSum or 0) / scene.sampleCount or 0
    lines[#lines + 1] = string.format(
      "- %s：%s，%.1f分钟，平均/最低FPS %.1f/%.1f，最长帧 %.1fms，50ms慢帧 %d；插件排行：%s",
      scene.kind or "场景", scene.zone or "未知区域", duration / 60, avg,
      scene.minFPS == 9999 and 0 or (scene.minFPS or 0), scene.maxFrameMs or 0,
      scene.over50Frames or 0, RaidOptimizerFormatScenePluginRanking(scene, 6)
    )
  end
  return #lines > 0 and table.concat(lines, "\n") or "- 未形成场景样本。请在目标区域停留至少一分钟后结束记录。"
end

-- The whole-session FPS number includes walking, loading, waiting and other
-- non-combat time.  It is useful as a recorder health signal, but it must not
-- be presented as the raid-performance score.  Build a separate, duration-
-- weighted score from ENCOUNTER_START..ENCOUNTER_END segments only.
local function BuildBossOnlyMetrics(record)
  local metrics = {
    count = 0,
    durationSec = 0,
    totalFrames = 0,
    totalFrameMs = 0,
    minFPS = nil,
    maxFrameMs = 0,
    over50Frames = 0,
    over100Frames = 0,
  }

  for _, boss in ipairs((record and record.bossSegments) or {}) do
    local frameMs = tonumber(boss.totalFrameMs) or 0
    local frames = tonumber(boss.totalFrames) or 0
    local sampleCount = tonumber(boss.sampleCount) or 0
    if frameMs > 0 and frames > 0 and sampleCount > 0 then
      metrics.count = metrics.count + 1
      metrics.durationSec = metrics.durationSec + math.max(0, (tonumber(boss.endAt) or 0) - (tonumber(boss.startAt) or 0))
      metrics.totalFrames = metrics.totalFrames + frames
      metrics.totalFrameMs = metrics.totalFrameMs + frameMs
      metrics.maxFrameMs = math.max(metrics.maxFrameMs, tonumber(boss.maxFrameMs) or 0)
      metrics.over50Frames = metrics.over50Frames + (tonumber(boss.over50Frames) or 0)
      metrics.over100Frames = metrics.over100Frames + (tonumber(boss.over100Frames) or 0)
      local minFPS = tonumber(boss.minFPS) or 0
      if minFPS > 0 and (not metrics.minFPS or minFPS < metrics.minFPS) then
        metrics.minFPS = minFPS
      end
    end
  end

  metrics.avgFPS = metrics.totalFrameMs > 0 and (metrics.totalFrames * 1000 / metrics.totalFrameMs) or 0
  metrics.minFPS = metrics.minFPS or 0
  return metrics
end

local function SummarizeNativeRecord(record, fullReport, samples, avg)
  if record.sceneMode then
    return string.format(
      "场景帧数体检报告\n==============================\n开始：%s\n记录时长：%.1f分钟\n\n场景排名（按每场景的慢帧命中优先，再按原生插件近期平均耗时）：\n%s\n\n判读边界：插件排行仅覆盖插件 Lua 耗时。每段还保留平均/最低FPS和最长帧；如果帧数很差但插件耗时很低，优先排查显卡、驱动、特效、客户端引擎或网络，不把插件误判为根因。",
      record.startedAtText or "", (record.elapsedSec or 0) / 60, RaidOptimizerSummarizeSceneSegments(record)
    )
  end
  local bossLines = {}
  for _, boss in ipairs(record.bossSegments or {}) do
    local count = boss.sampleCount or 0
    local bossAvg = count > 0 and ((boss.fpsSum or 0) / count) or 0
    local duration = math.max(0, (boss.endAt or boss.startAt or 0) - (boss.startAt or 0))
    local cpu = boss.cpuBoundSamples or {}
    local profiler = boss.nativeProfilerEnd or {}
    bossLines[#bossLines + 1] = string.format(
      "- %s：%s，%.1f分，平均/最低FPS %.1f/%.1f，最长帧 %.1fms；应用/插件/非插件 %.2f/%.2f/%.2fms，插件占比 %.1f%%（%s）；CPU判定 %d/%d/%d；5秒排行 %d次（空%d/失败%d）；平均Top：%s；尖峰Top：%s；10ms次数Top：%s",
      tostring(boss.name or "未知Boss"),
      boss.incomplete and "记录中断" or (boss.success and "击杀" or "结束"),
      duration / 60,
      bossAvg,
      boss.minFPS == 9999 and 0 or (boss.minFPS or 0),
      boss.maxFrameMs or 0,
      boss.applicationEncounterMs or 0,
      boss.addonEncounterMs or 0,
      boss.nonAddonEncounterMs or 0,
      (boss.addonShare or 0) * 100,
      boss.nativeProfilerAverageSource == "RecentAverageTime" and "最近平均回退" or "整场平均",
      cpu.cpu or 0,
      cpu.notCpu or 0,
      cpu.unknown or 0,
      boss.nativeProfilerRecentAttemptCount or 0,
      boss.nativeProfilerRecentEmptyResults or 0,
      boss.nativeProfilerRecentFailures or 0,
      FormatNativeTopAddOns(profiler.topAddOns, fullReport and 8 or 4),
      FormatNativeTopAddOns(profiler.topAddOnsPeak, fullReport and 8 or 4),
      FormatNativeTopAddOns(profiler.topAddOnsOver10Ms, fullReport and 8 or 4)
    )
  end
  if #bossLines == 0 then bossLines[1] = "- 未记录到Boss分段。" end
  local capability = record.nativeProfilerCapability or {}
  local recentStats = record.nativeProfilerRecentStats or {}
  local mode = fullReport and "完整报告" or "短报告"
  local bossOnly = BuildBossOnlyMetrics(record)
  return string.format(
    "原生黑匣子%s\n==============================\n开始：%s\n记录全程：%.1f分钟（不计入副本成绩）\nBoss战斗（%d场，%.1f分钟）平均/最低FPS：%.1f/%.1f\nBoss战斗最长单帧：%.1fms，50/100ms慢帧：%d/%d\n原生插件账本：%s（IsEnabled仅记录不作闸门：%s）\n5秒插件排行：%d次，成功%d，空%d，失败%d，不可用%d；总/最大采样开销 %.2f/%.2fms\n原生战斗日志：%s\n慢帧精确记录：%d条，覆盖丢弃：%d条\nWA原生归因：%s\n\nBoss：\n%s\n\n说明：副本成绩只使用 ENCOUNTER_START 到 ENCOUNTER_END 之间的实际帧数，跑图、等待、界面停留和空闲时间一律不计入；不启用scriptProfile，不读取CLEU内容，不包装WA、Details或机器人。技能和事件洪峰由退出后的WoWCombatLog离线对时。WA深度归因是单独诊断窗，不计入FPS验收。",
    mode,
    record.startedAtText or "",
    (record.elapsedSec or 0) / 60,
    bossOnly.count,
    bossOnly.durationSec / 60,
    bossOnly.avgFPS,
    bossOnly.minFPS,
    bossOnly.maxFrameMs,
    bossOnly.over50Frames,
    bossOnly.over100Frames,
    capability.available and "可用" or "不可用",
    tostring(capability.reportedEnabled),
    recentStats.attempts or 0,
    recentStats.successful or 0,
    recentStats.emptyResults or 0,
    recentStats.failures or 0,
    recentStats.unavailable or 0,
    recentStats.totalCaptureMs or 0,
    recentStats.maxCaptureMs or 0,
    record.rawCombatLogCaptured and "已由魔兽写盘" or "未能开启",
    #(record.nativeSlowFrames or {}),
    record.nativeSlowFramesDropped or 0,
    FormatWeakAurasProfile(record.waProfile, fullReport),
    table.concat(bossLines, "\n")
  )
end

local function SummarizeRaidRecord(record, fullReport)
  if not record or not record.samples or #record.samples == 0 then
    return "还没有实战记录。打本前点“开始记录”，整场打完点“结束保存”。"
  end
  local samples = OrderedSamples(record)
  local fpsCount = record.fpsSampleCount
  if fpsCount == nil then fpsCount = record.sampleCount or 0 end
  local avg = fpsCount > 0 and ((record.fpsSum or 0) / fpsCount) or 0
  if record.nativeOnly then
    return SummarizeNativeRecord(record, fullReport, samples, avg)
  end
  local sorted = {}
  for _, sample in ipairs(samples) do
    if sample.fpsValid ~= false and (sample.fps or 0) > 0 then
      sorted[#sorted + 1] = sample
    end
  end
  table.sort(sorted, function(a, b) return (a.fps or 9999) < (b.fps or 9999) end)
  local badLines = {}
  for index = 1, math.min(5, #sorted) do
    local s = sorted[index]
    badLines[#badLines + 1] = string.format(
      "- %02d:%02d FPS %.1f，最长帧 %.1fms，姓名板 %d，Lua错误 %d，LGF %d",
      math.floor((s.t or 0) / 60),
      (s.t or 0) % 60,
      s.fps or 0,
      s.worstFrameMs or 0,
      s.plates or 0,
      s.luaErrors or 0,
      s.lgfQueries or 0
    )
    if s.topSpells and s.topSpells ~= "" then
      badLines[#badLines] = badLines[#badLines] .. "\n  附近高频技能：" .. s.topSpells
    end
    if s.logCategories and #s.logCategories > 0 then
      badLines[#badLines] = badLines[#badLines] .. "\n  日志分类：" .. FormatCounterRows(s.logCategories, 5)
    end
    if s.logScopes and #s.logScopes > 0 then
      badLines[#badLines] = badLines[#badLines] .. "\n  来源范围：" .. FormatCounterRows(s.logScopes, 4)
    end
  end
  if #badLines == 0 then badLines[1] = "- 暂无明显低帧样本。" end

  local categoryRows = DeltaCounter(record.logCategories or {}, nil)
  local scopeRows = DeltaCounter(record.logScopes or {}, nil)
  local luaErrorRows = DeltaCounter(record.luaErrorAddons or {}, nil)
  local luaMessageRows = DeltaCounter(record.luaErrorMessages or {}, nil)
  if not fullReport then
    local briefBadLines = {}
    for index = 1, math.min(3, #sorted) do
      local s = sorted[index]
      if s then
        local details = { "技能：" .. TrimTopSpellText(s.topSpells, 3) }
        local categoryText = FormatCounterRows(s.logCategories, 3)
        local scopeText = FormatCounterRows(s.logScopes, 3)
        if categoryText ~= "无" then details[#details + 1] = "分类：" .. categoryText end
        if scopeText ~= "无" then details[#details + 1] = "来源：" .. scopeText end
        briefBadLines[#briefBadLines + 1] = string.format(
          "- %02d:%02d FPS %.1f，最长帧 %.1fms，事件 %d，中枢去重估算 %.2fms，机器人短窗实测 %.2fms，GC %d，姓名板 %d，%s",
          math.floor((s.t or 0) / 60),
          (s.t or 0) % 60,
          s.fps or 0,
          s.worstFrameMs or 0,
          s.combatLogs or 0,
          s.hubMeasuredMs or 0,
          s.robotMs or 0,
          s.gcDrops or 0,
          s.plates or 0,
          table.concat(details, "，")
        )
      end
    end
    if #briefBadLines == 0 then briefBadLines[1] = "- 暂无明显低帧样本。" end
    return string.format(
      "流畅度诊断短报告（低开销飞行记录）\n==============================\n开始：%s  时长：%.1f 分钟\n平均/最低 FPS：%.1f / %.1f\n最长单帧：%.1fms，>50ms：%d，>100ms：%d\n战斗事件峰值：%d/秒，Lua错误峰值：%d/秒，LGF：%d/秒\n抽样归因峰值/秒：中枢去重估算 %.2fms（简单相加上界 %.2fms），WA回调估算 %.2fms，光环刷新估算 %.2fms，机器人短窗实测 %.2fms，Details刷新次数 %d\nGC回落：%d次，Lua增长峰值：%.0fKB，记录器采样峰值：%.2fms\n原始战斗日志：%s\n\n最低帧附近：\n%s\n\n加载验证：%s\n\n说明：不开插件CPU监控、不逐条二次解析战斗事件；Hub/WA/光环时间来自稀疏抽样估算，机器人是短窗实测，Details当前只记录刷新次数。未命中的剩余部分仍包含未插桩Lua、暴雪主线程与渲染等待。",
      record.startedAtText or "",
      ((samples[#samples] and samples[#samples].t) or 0) / 60,
      avg,
      record.minFPS == 9999 and 0 or (record.minFPS or 0),
      record.maxFrameMs or 0,
      record.over50Frames or 0,
      record.over100Frames or 0,
      record.maxCombatLogsPerSecond or record.maxCombatLogs or 0,
      record.maxLuaErrors or 0,
      record.maxLgfQueries or 0,
      record.maxHubMeasuredMs or 0,
      record.maxHubEstimatedUpperMs or record.maxHubMeasuredMs or 0,
      record.maxWAConsumerMs or 0,
      record.maxAuraRefreshMs or 0,
      record.maxRobotMs or 0,
      math.max(record.maxDetailsRefreshes or 0, record.maxDetailsRowRefreshes or 0),
      record.totalGcDrops or 0,
      record.maxLuaGrowthKB or 0,
      record.maxRecorderSampleMs or 0,
      record.rawCombatLogCaptured and "已持续写盘" or "未能开启",
      table.concat(briefBadLines, "\n"),
      RuntimeValidationBriefText()
    )
  end

  local eventLines = {}
  for index = math.max(1, #(record.events or {}) - 8), #(record.events or {}) do
    local e = record.events[index]
    if e then
      eventLines[#eventLines + 1] = string.format("- %02d:%02d %s：%s", math.floor((e.t or 0) / 60), (e.t or 0) % 60, e.kind or "事件", e.text or "")
    end
  end
  if #eventLines == 0 then eventLines[1] = "- 暂无嗜血/Boss/掉帧事件。" end

  local bossLines = {}
  for _, boss in ipairs(record.bossSegments or {}) do
    local count = boss.sampleCount or 0
    local bossAvg = count > 0 and ((boss.fpsSum or 0) / count) or 0
    local duration = ((boss.endT or boss.startT or 0) - (boss.startT or 0))
    local status = boss.success and "击杀" or (boss.active and "进行中" or "结束")
    bossLines[#bossLines + 1] = string.format(
      "- %s：%s，时长 %.1f 分，平均/最低 FPS %.1f / %.1f，最长帧 %.1fms，事件峰值 %d/秒，Lua错误峰值 %d，LGF峰值 %d，姓名板峰值 %d",
      boss.name or "未知Boss",
      status,
      math.max(duration, 0) / 60,
      bossAvg,
      boss.minFPS == 9999 and 0 or (boss.minFPS or 0),
      boss.maxFrameMs or 0,
      boss.maxCombatLogsPerSecond or boss.maxCombatLogs or 0,
      boss.maxLuaErrors or 0,
      boss.maxLgfQueries or 0,
      boss.maxPlates or 0
    )
    bossLines[#bossLines] = bossLines[#bossLines] .. "\n  高频技能：" .. FormatTopSpellTotalsBrief(boss, 5)
    if boss.luaErrorAddons then
      bossLines[#bossLines] = bossLines[#bossLines] .. "\n  Lua错误来源：" .. FormatCounterRows(DeltaCounter(boss.luaErrorAddons, nil), 4)
    end
    if boss.logCategories then
      bossLines[#bossLines] = bossLines[#bossLines] .. "\n  日志分类：" .. FormatCounterRows(DeltaCounter(boss.logCategories, nil), 5)
    end
    for index = 1, math.min(3, #(boss.lowSamples or {})) do
      local s = boss.lowSamples[index]
      bossLines[#bossLines] = bossLines[#bossLines] .. string.format(
        "\n  低帧点 %02d:%02d FPS %.1f，最长帧 %.1fms，事件 %d，Lua错误 %d，LGF %d",
        math.floor((s.t or 0) / 60),
        (s.t or 0) % 60,
        s.fps or 0,
        s.worstFrameMs or 0,
        s.combatLogs or 0,
        s.luaErrors or 0,
        s.lgfQueries or 0
      )
    end
  end
  if #bossLines == 0 then bossLines[1] = "- 未记录到 Boss 分段。请确认从进本前开始记录，并打到 Boss 开始/结束事件。" end

  local modeName = "低开销时间轴模式"
  return string.format(
    "流畅度诊断完整报告（%s）\n==============================\n开始：%s\n最近更新：%s\n记录时长：%.1f 分钟\n平均 FPS：%.1f\n最低 FPS：%.1f\n最长单帧：%.1fms\n33/50/100ms 慢帧：%d / %d / %d\n最大LibGetFrame查询：%d/秒\n最大战斗事件：%d/秒\n最大Lua错误：%d/秒，总计 %d\n抽样归因峰值/秒：中枢去重估算 %.2fms（简单相加上界 %.2fms），WA回调估算 %.2fms，光环刷新估算 %.2fms，机器人短窗实测 %.2fms\nDetails刷新次数峰值：%d/秒，GC回落：%d次，Lua增长峰值：%.0fKB\n归因短窗：%d次，记录器采样峰值：%.2fms\n原始战斗日志：%s\n\nBoss 分段汇总：\n%s\n\nLua错误归因：\n来源：%s\n高频错误：%s\n\n最低帧附近样本：\n%s\n\n本次记录有效性：\n%s\n\n自动事件：\n%s\n\n说明：本模式不开 scriptProfile/插件CPU统计，也不在游戏中逐条解析技能。Hub/WA/光环时间来自稀疏抽样估算，机器人是短窗实测，Details当前只记录刷新次数。插件Lua看不到GPU、驱动和暴雪引擎内部，剩余值仍只叫“未归因”。",
    modeName,
    record.startedAtText or "",
    record.updatedAtText or "",
    ((samples[#samples] and samples[#samples].t) or 0) / 60,
    avg,
    record.minFPS == 9999 and 0 or (record.minFPS or 0),
    record.maxFrameMs or 0,
    record.over33Frames or 0,
    record.over50Frames or 0,
    record.over100Frames or 0,
    record.maxLgfQueries or 0,
    record.maxCombatLogsPerSecond or record.maxCombatLogs or 0,
    record.maxLuaErrors or 0,
    record.luaErrorCount or 0,
    record.maxHubMeasuredMs or 0,
    record.maxHubEstimatedUpperMs or record.maxHubMeasuredMs or 0,
    record.maxWAConsumerMs or 0,
    record.maxAuraRefreshMs or 0,
    record.maxRobotMs or 0,
    math.max(record.maxDetailsRefreshes or 0, record.maxDetailsRowRefreshes or 0),
    record.totalGcDrops or 0,
    record.maxLuaGrowthKB or 0,
    record.attributionWindows or 0,
    record.maxRecorderSampleMs or 0,
    record.rawCombatLogCaptured and "已持续写盘" or "未能开启",
    table.concat(bossLines, "\n"),
    FormatCounterRows(luaErrorRows, 8),
    FormatCounterRows(luaMessageRows, 6),
    table.concat(badLines, "\n"),
    RuntimeValidationText(),
    table.concat(eventLines, "\n")
  )
end

local function BuildRaidExport(record, reportText)
  if not record or not record.samples or #record.samples == 0 then return nil end
  local samples = OrderedSamples(record)
  local fpsCount = record.fpsSampleCount
  if fpsCount == nil then fpsCount = record.sampleCount or 0 end
  local avg = fpsCount > 0 and ((record.fpsSum or 0) / fpsCount) or 0
  local duration = ((samples[#samples] and samples[#samples].t) or 0)
  local lowFpsSamples = 0
  for _, sample in ipairs(samples) do
    if sample.fpsValid ~= false and (sample.fps or 0) > 0 and (sample.fps or 0) <= LOW_FPS_THRESHOLD then
      lowFpsSamples = lowFpsSamples + 1
    end
  end
  return {
    format = "RO-TSD-1",
    schemaRevision = record.schemaRevision or 1,
    addonVersion = ADDON_VERSION,
    generatedAt = date("%Y-%m-%d %H:%M:%S"),
    startedAt = record.startedAtText or "",
    endedAt = record.endedAtText or record.updatedAtText or "",
    durationSec = duration,
    sampleCount = record.sampleCount or #samples,
    samplesRetained = #samples,
    samplesDropped = record.samplesDropped or 0,
    timelineComplete = not record.samplesWrapped,
    fpsSampleCount = fpsCount,
    totalFrames = record.totalFrames or 0,
    totalFrameMs = record.totalFrameMs or 0,
    eventCount = #(record.events or {}),
    avgFPS = avg,
    minFPS = record.minFPS == 9999 and 0 or (record.minFPS or 0),
    lowFpsSamples = lowFpsSamples,
    maxCpuDelta = record.maxCpuDelta or 0,
    maxWAEvents = record.maxWAEvents or 0,
    maxCombatLogs = record.maxCombatLogs or 0,
    maxLuaErrors = record.maxLuaErrors or 0,
    luaErrorCount = record.luaErrorCount or 0,
    luaErrorAddons = record.luaErrorAddons or {},
    luaErrorMessages = record.luaErrorMessages or {},
    luaErrorRecent = record.luaErrorRecent or {},
    maxLgfQueries = record.maxLgfQueries or 0,
    bossCount = #(record.bossSegments or {}),
    maxFrameMs = record.maxFrameMs or 0,
    over33Frames = record.over33Frames or 0,
    over50Frames = record.over50Frames or 0,
    over100Frames = record.over100Frames or 0,
    maxCombatLogsPerSecond = record.maxCombatLogsPerSecond or 0,
    combatRateSource = record.combatRateSource or "native-count",
    rawCombatLogCaptured = record.rawCombatLogCaptured and true or false,
    rawCombatLogRestarts = record.rawCombatLogRestarts or 0,
    maxHubMeasuredMs = record.maxHubMeasuredMs or 0,
    maxHubEstimatedMs = record.maxHubEstimatedMs or record.maxHubMeasuredMs or 0,
    maxHubEstimatedUpperMs = record.maxHubEstimatedUpperMs or record.maxHubMeasuredMs or 0,
    hubTimingMode = "sampled-estimate-nonoverlap",
    maxWAConsumerMs = record.maxWAConsumerMs or 0,
    maxAuraRefreshMs = record.maxAuraRefreshMs or 0,
    maxRobotMs = record.maxRobotMs or 0,
    maxRobotCallMs = record.maxRobotCallMs or 0,
    maxDetailsRefreshes = record.maxDetailsRefreshes or 0,
    maxDetailsRowRefreshes = record.maxDetailsRowRefreshes or 0,
    totalGcDrops = record.totalGcDrops or 0,
    maxLuaGrowthKB = record.maxLuaGrowthKB or 0,
    attributionWindows = record.attributionWindows or 0,
    attributionReasons = record.attributionReasons or {},
    recorderSampleMs = record.recorderSampleMs or 0,
    maxRecorderSampleMs = record.maxRecorderSampleMs or 0,
    autoStarted = record.autoStarted and true or false,
    startReason = record.startReason,
    continuationOf = record.continuationOf,
    runId = record.runId,
    nativeOnly = record.nativeOnly and true or false,
    monotonicSource = record.monotonicSource,
    startedMonotonicSec = record.startedMonotonicSec,
    startedUnixSec = record.startedUnixSec,
    startedServerUnixSec = record.startedServerUnixSec,
    startClockAnchor = record.startClockAnchor,
    endClockAnchor = record.endClockAnchor,
    clockAnchors = record.clockAnchors or {},
    nativeSlowFrames = OrderedNativeSlowFrames(record),
    nativeSlowFramesDropped = record.nativeSlowFramesDropped or 0,
    nativeProfilerCapability = record.nativeProfilerCapability,
    nativeProfilerRecentStats = record.nativeProfilerRecentStats,
    cpuBoundSamples = record.cpuBoundSamples,
    cpuBoundErrors = record.cpuBoundErrors or 0,
    sceneMode = record.sceneMode and true or false,
    sceneSegments = record.sceneSegments or {},
    rawCombatLogSession = record.rawCombatLogSession,
    samples = samples,
    bossSegments = record.bossSegments or {},
    events = record.events or {},
    combatLogCategories = record.logCategories or {},
    combatLogScopes = record.logScopes or {},
    cpuProfile = record.deepCpu and true or false,
    waProfileCapture = record.waProfileCapture and true or false,
    waProfile = record.waProfile,
    reportText = reportText or SummarizeRaidRecord(record, false),
    fullReportText = SummarizeRaidRecord(record, true),
  }
end

-- latestRaidExport intentionally contains the complete, most recent run.  A
-- short `/reload`, dummy pull, or logout check can therefore replace the only
-- detailed record from a five-Boss raid.  Keep one compact, Boss-only export
-- beside it.  It is diagnostic data only: no extra in-combat work, no UI
-- hooks, and no combat-log parsing are introduced.
local function IsWithinBossWindow(row, boss)
  local at = tonumber(row and (row.atPrecise or row.atSec or row.at))
  local started = tonumber(boss and boss.startAt)
  local ended = tonumber(boss and boss.endAt)
  return at and started and ended and at >= started and at <= ended
end

local function CopyBossTimelineRow(row)
  return {
    seq = tonumber(row and row.seq) or 0,
    bossIndex = tonumber(row and row.bossIndex) or nil,
    at = tonumber(row and (row.at or row.atSec)) or 0,
    atPrecise = tonumber(row and (row.atPrecise or row.atSec)) or nil,
    t = tonumber(row and row.t) or 0,
    estimatedUnixSec = tonumber(row and row.estimatedUnixSec) or nil,
    fps = tonumber(row and row.fps) or 0,
    fpsValid = row and row.fpsValid ~= false or false,
    fpsWindowMs = tonumber(row and row.fpsWindowMs) or 0,
    fpsWindowStartAt = tonumber(row and row.fpsWindowStartAt) or nil,
    fpsWindowEndAt = tonumber(row and row.fpsWindowEndAt) or nil,
    fpsFrameCount = tonumber(row and row.fpsFrameCount) or 0,
    worstFrameMs = tonumber(row and row.worstFrameMs) or 0,
    over33Frames = tonumber(row and row.over33Frames) or 0,
    over50Frames = tonumber(row and row.over50Frames) or 0,
    over100Frames = tonumber(row and row.over100Frames) or 0,
    cpuBound = row and row.cpuBound or nil,
    combatLogs = tonumber(row and row.combatLogs) or 0,
    luaErrors = tonumber(row and row.luaErrors) or 0,
    lgfQueries = tonumber(row and row.lgfQueries) or 0,
    nativeOnly = row and row.nativeOnly and true or false,
    reason = tostring(row and row.reason or ""),
  }
end

local function BuildBossDiagnosticExport(export)
  if type(export) ~= "table" then return nil end
  local bossIndexes, bosses = {}, {}
  for index, boss in ipairs(export.bossSegments or {}) do
    if (tonumber(boss.sampleCount) or 0) > 0
      and (tonumber(boss.totalFrames) or 0) > 0
      and (tonumber(boss.totalFrameMs) or 0) > 0
      and tonumber(boss.endAt) ~= nil
    then
      bossIndexes[index] = true
      -- Boss summaries are bounded (five low samples plus sparse profiler
      -- rows), so retaining these native fields is inexpensive and preserves
      -- the evidence needed for attribution.
      bosses[#bosses + 1] = boss
    end
  end
  if #bosses == 0 then return nil end

  local samples, slowFrames, events = {}, {}, {}
  local function BelongsToBoss(row)
    if bossIndexes[tonumber(row and row.bossIndex)] then return true end
    for _, boss in ipairs(bosses) do
      if IsWithinBossWindow(row, boss) then return true end
    end
    return false
  end
  for _, sample in ipairs(export.samples or {}) do
    if BelongsToBoss(sample) then samples[#samples + 1] = CopyBossTimelineRow(sample) end
  end
  for _, slow in ipairs(export.nativeSlowFrames or {}) do
    if BelongsToBoss(slow) then slowFrames[#slowFrames + 1] = CopyBossTimelineRow(slow) end
  end
  for _, event in ipairs(export.events or {}) do
    if BelongsToBoss(event) then
      events[#events + 1] = {
        at = tonumber(event.at) or 0,
        t = tonumber(event.t) or 0,
        kind = tostring(event.kind or ""),
        text = tostring(event.text or ""),
      }
    end
  end

  local metrics = BuildBossOnlyMetrics({ bossSegments = bosses })
  return {
    format = "RO-TSD-BOSS-1",
    sourceFormat = export.format,
    schemaRevision = export.schemaRevision,
    addonVersion = export.addonVersion,
    generatedAt = export.generatedAt,
    startedAt = export.startedAt,
    endedAt = export.endedAt,
    runId = export.runId,
    nativeOnly = export.nativeOnly and true or false,
    bossTimelineOnly = true,
    bossCount = #bosses,
    bossDurationSec = metrics.durationSec,
    bossAvgFPS = metrics.avgFPS,
    bossMinFPS = metrics.minFPS,
    bossMaxFrameMs = metrics.maxFrameMs,
    bossOver50Frames = metrics.over50Frames,
    bossOver100Frames = metrics.over100Frames,
    rawCombatLogSession = export.rawCombatLogSession,
    clockAnchors = export.clockAnchors or {},
    bossSegments = bosses,
    samples = samples,
    nativeSlowFrames = slowFrames,
    events = events,
  }
end

local function BuildRaidHistorySummary(export)
  if type(export) ~= "table" then return nil end
  local bossOnly = BuildBossOnlyMetrics(export)
  local bossNames = {}
  local bossSummaries = {}
  for _, boss in ipairs(export.bossSegments or {}) do
    bossNames[#bossNames + 1] = tostring(boss.name or "未知Boss")
    local count = tonumber(boss.sampleCount) or 0
    local function CompactTopRows(source)
      local rows = {}
      for index = 1, math.min(5, #(source or {})) do
        local row = source[index]
        rows[#rows + 1] = {
          addOnName = tostring(row.addOnName or ""),
          metricValue = tonumber(row.metricValue) or 0,
        }
      end
      return rows
    end
    local profilerEnd = boss.nativeProfilerEnd
    local recentSamples = boss.nativeProfilerRecentSamples or {}
    local firstRecent = recentSamples[1]
    local lastRecent = recentSamples[#recentSamples]
    local recentMetricSampleCount = 0
    local recentApplicationSum = 0
    local recentAddonSum = 0
    local recentTopTotals = {}
    for _, recent in ipairs(recentSamples) do
      local applicationMs = tonumber(recent.applicationRecentMs)
      local addonMs = tonumber(recent.addonRecentMs)
      if IsFiniteNumber(applicationMs) and applicationMs > 0
        and IsFiniteNumber(addonMs) and addonMs >= 0 then
        recentMetricSampleCount = recentMetricSampleCount + 1
        recentApplicationSum = recentApplicationSum + applicationMs
        recentAddonSum = recentAddonSum + addonMs
      end
      if recent.status == "ok" then
        for _, row in ipairs(recent.topAddOns or {}) do
          local addOnName = tostring(row.addOnName or "")
          local metricValue = tonumber(row.metricValue)
          if addOnName ~= "" and IsFiniteNumber(metricValue) then
            local total = recentTopTotals[addOnName]
            if not total then
              total = { addOnName = addOnName, sum = 0, observations = 0 }
              recentTopTotals[addOnName] = total
            end
            total.sum = total.sum + metricValue
            total.observations = total.observations + 1
          end
        end
      end
    end
    local recentTopAddOnsAverage = {}
    for _, total in pairs(recentTopTotals) do
      recentTopAddOnsAverage[#recentTopAddOnsAverage + 1] = {
        addOnName = total.addOnName,
        metricValue = total.observations > 0 and (total.sum / total.observations) or 0,
        observations = total.observations,
      }
    end
    table.sort(recentTopAddOnsAverage, function(left, right)
      if left.metricValue == right.metricValue then
        return left.addOnName < right.addOnName
      end
      return left.metricValue > right.metricValue
    end)
    while #recentTopAddOnsAverage > 8 do table.remove(recentTopAddOnsAverage) end
    local worstWindows = {}
    for index = 1, math.min(5, #(boss.lowSamples or {})) do
      local row = boss.lowSamples[index]
      worstWindows[#worstWindows + 1] = {
        t = tonumber(row.t) or 0,
        fps = tonumber(row.fps) or 0,
        worstFrameMs = tonumber(row.worstFrameMs) or 0,
        combatLogs = tonumber(row.combatLogs) or 0,
      }
    end
    bossSummaries[#bossSummaries + 1] = {
      encounterId = tonumber(boss.encounterId) or 0,
      name = tostring(boss.name or "未知Boss"),
      difficultyId = tonumber(boss.difficultyId) or 0,
      groupSize = tonumber(boss.groupSize) or 0,
      success = boss.success,
      incomplete = boss.incomplete and true or false,
      durationSec = math.max(0, (tonumber(boss.endAt) or 0) - (tonumber(boss.startAt) or 0)),
      sampleCount = count,
      avgFPS = count > 0 and ((tonumber(boss.fpsSum) or 0) / count) or 0,
      minFPS = boss.minFPS == 9999 and 0 or (tonumber(boss.minFPS) or 0),
      totalFrames = tonumber(boss.totalFrames) or 0,
      totalFrameMs = tonumber(boss.totalFrameMs) or 0,
      maxFrameMs = tonumber(boss.maxFrameMs) or 0,
      over50Frames = tonumber(boss.over50Frames) or 0,
      over100Frames = tonumber(boss.over100Frames) or 0,
      maxLuaErrors = tonumber(boss.maxLuaErrors) or 0,
      applicationEncounterMs = tonumber(boss.applicationEncounterMs) or 0,
      addonEncounterMs = tonumber(boss.addonEncounterMs) or 0,
      nonAddonEncounterMs = tonumber(boss.nonAddonEncounterMs) or 0,
      nativeProfilerAverageSource = boss.nativeProfilerAverageSource,
      candidateIdentity = boss.candidateIdentityEnd or boss.candidateIdentityStart,
      candidateTelemetryAvailable = boss.candidateTelemetryAvailable and true or false,
      candidateRouteDelta = boss.candidateRouteDelta,
      topAddOns = CompactTopRows(profilerEnd and profilerEnd.topAddOns),
      topAddOnsSource = profilerEnd and profilerEnd.topAddOnsSource,
      topAddOnsPeak = CompactTopRows(profilerEnd and profilerEnd.topAddOnsPeak),
      topAddOnsOver5Ms = CompactTopRows(profilerEnd and profilerEnd.topAddOnsOver5Ms),
      topAddOnsOver10Ms = CompactTopRows(profilerEnd and profilerEnd.topAddOnsOver10Ms),
      recentTopAddOnSampleCount = #recentSamples,
      recentTopAddOnSamplesDropped = tonumber(boss.nativeProfilerRecentSamplesDropped) or 0,
      recentTopAddOnEmptyResults = tonumber(boss.nativeProfilerRecentEmptyResults) or 0,
      recentTopAddOnFailures = tonumber(boss.nativeProfilerRecentFailures) or 0,
      recentTopAddOnMaxCaptureMs = tonumber(boss.nativeProfilerRecentMaxCaptureMs) or 0,
      recentMetricSampleCount = recentMetricSampleCount,
      recentApplicationAverageMs = recentMetricSampleCount > 0 and (recentApplicationSum / recentMetricSampleCount) or 0,
      recentAddonAverageMs = recentMetricSampleCount > 0 and (recentAddonSum / recentMetricSampleCount) or 0,
      recentNonAddonAverageMs = recentMetricSampleCount > 0 and math.max(0, (recentApplicationSum - recentAddonSum) / recentMetricSampleCount) or 0,
      recentTopAddOnsAverage = recentTopAddOnsAverage,
      recentTopAddOnsStart = CompactTopRows(firstRecent and firstRecent.topAddOns),
      recentTopAddOnsEnd = CompactTopRows(lastRecent and lastRecent.topAddOns),
      recentTopAddOnsStartStatus = firstRecent and firstRecent.status,
      recentTopAddOnsEndStatus = lastRecent and lastRecent.status,
      worstWindows = worstWindows,
    }
  end
  return {
    format = "RO-TSD-SUMMARY-1",
    schemaRevision = export.schemaRevision or 1,
    addonVersion = export.addonVersion or ADDON_VERSION,
    generatedAt = export.generatedAt,
    startedAt = export.startedAt,
    endedAt = export.endedAt,
    durationSec = export.durationSec or 0,
    sampleCount = export.sampleCount or 0,
    samplesRetained = export.samplesRetained or 0,
    samplesDropped = export.samplesDropped or 0,
    timelineComplete = export.timelineComplete ~= false,
    fpsSampleCount = export.fpsSampleCount or export.sampleCount or 0,
    avgFPS = export.avgFPS or 0,
    minFPS = export.minFPS or 0,
    bossCount = export.bossCount or 0,
    bossNames = table.concat(bossNames, " / "),
    bossSummaries = bossSummaries,
    -- Canonical comparison fields.  Consumers must use these instead of the
    -- session-wide values above when comparing raid performance.
    bossOnlyCount = bossOnly.count,
    bossOnlyDurationSec = bossOnly.durationSec,
    bossOnlyTotalFrames = bossOnly.totalFrames,
    bossOnlyTotalFrameMs = bossOnly.totalFrameMs,
    bossOnlyAvgFPS = bossOnly.avgFPS,
    bossOnlyMinFPS = bossOnly.minFPS,
    bossOnlyMaxFrameMs = bossOnly.maxFrameMs,
    bossOnlyOver50Frames = bossOnly.over50Frames,
    bossOnlyOver100Frames = bossOnly.over100Frames,
    maxFrameMs = export.maxFrameMs or 0,
    over50Frames = export.over50Frames or 0,
    over100Frames = export.over100Frames or 0,
    maxCombatLogs = export.maxCombatLogs or 0,
    luaErrorCount = export.luaErrorCount or 0,
    maxHubEstimatedMs = export.maxHubEstimatedMs or export.maxHubMeasuredMs or 0,
    maxRobotMs = export.maxRobotMs or 0,
    totalGcDrops = export.totalGcDrops or 0,
    autoStarted = export.autoStarted and true or false,
    startReason = export.startReason,
    continuationOf = export.continuationOf,
    runId = export.runId,
    nativeOnly = export.nativeOnly and true or false,
    nativeProfilerAvailable = export.nativeProfilerCapability and export.nativeProfilerCapability.available and true or false,
    nativeProfilerRecentStats = export.nativeProfilerRecentStats,
    reportText = export.reportText,
  }
end

local function CompactRaidHistory()
  if not db then return end
  db.raidHistory = db.raidHistory or {}
  for index, entry in ipairs(db.raidHistory) do
    if type(entry) == "table" and (entry.samples ~= nil or entry.format ~= "RO-TSD-SUMMARY-1") then
      db.raidHistory[index] = BuildRaidHistorySummary(entry) or entry
    end
  end
  while #db.raidHistory > 4 do table.remove(db.raidHistory) end
end

local function HandleHubCombatRate(snapshot)
  if not recorderActive or not db or not db.raidRecord or type(snapshot) ~= "table" then return end
  local record = db.raidRecord
  local rate = math.max(0, tonumber(snapshot.rate) or 0)
  combatLogEvents = combatLogEvents + rate
  record.lastCombatRate = rate
  record.lastCombatRateAt = Now()
  record.maxCombatLogsPerSecond = math.max(record.maxCombatLogsPerSecond or 0, rate)
  local boss = CurrentBossSegment(record)
  if boss then
    boss.maxCombatLogsPerSecond = math.max(boss.maxCombatLogsPerSecond or 0, rate)
  end
end

local function StartHubRate(record)
  hubRateSubscribed = false
  if not record or not RaidOptimizerHub or type(RaidOptimizerHub.Subscribe) ~= "function" then return false end
  local ok, subscribed = pcall(RaidOptimizerHub.Subscribe, HUB_RATE_OWNER, "combat.rate", HandleHubCombatRate)
  hubRateSubscribed = ok and subscribed and true or false
  record.combatRateSource = hubRateSubscribed and "hub" or "native-count"
  return hubRateSubscribed
end

local function StopHubRate()
  if hubRateSubscribed and RaidOptimizerHub and type(RaidOptimizerHub.Unsubscribe) == "function" then
    pcall(RaidOptimizerHub.Unsubscribe, HUB_RATE_OWNER, "combat.rate")
  end
  hubRateSubscribed = false
end

local function StartRawCombatLog(record)
  if not record or type(LoggingCombat) ~= "function" then return false end
  local version, buildNumber, buildDate, tocVersion = SafeCall(GetBuildInfo)
  local instanceName, instanceType, difficultyId, difficultyName, maxPlayers, _, _, instanceId = SafeCall(GetInstanceInfo)
  record.rawCombatLogSession = {
    runId = record.runId,
    requestedAnchor = CaptureClockAnchor(record, "raw_log_start_request"),
    advancedCombatLogging = type(GetCVar) == "function" and tostring(SafeCall(GetCVar, "advancedCombatLogging") or "unknown") or "unknown",
    buildVersion = version,
    buildNumber = buildNumber,
    buildDate = buildDate,
    tocVersion = tocVersion,
    projectId = WOW_PROJECT_ID,
    instanceName = instanceName,
    instanceType = instanceType,
    difficultyId = difficultyId,
    difficultyName = difficultyName,
    maxPlayers = maxPlayers,
    instanceId = instanceId,
    restarts = {},
  }
  local okBefore, wasEnabled = pcall(LoggingCombat)
  record.rawCombatLogSession.wasEnabled = okBefore and wasEnabled and true or false
  if not okBefore then
    record.rawCombatLogSession.confirmed = false
    return false
  end
  record.rawCombatLogWasEnabled = wasEnabled and true or false
  record.rawCombatLogOwned = false
  if not wasEnabled then
    local okEnable = pcall(LoggingCombat, true)
    local okAfter, enabledAfter = pcall(LoggingCombat)
    record.rawCombatLogOwned = okEnable and okAfter and enabledAfter and true or false
  end
  local okNow, enabledNow = pcall(LoggingCombat)
  record.rawCombatLogCaptured = okNow and enabledNow and true or false
  record.rawCombatLogSession.owned = record.rawCombatLogOwned and true or false
  record.rawCombatLogSession.confirmed = record.rawCombatLogCaptured and true or false
  record.rawCombatLogSession.confirmedAnchor = CaptureClockAnchor(record, "raw_log_start_confirmed")
  return record.rawCombatLogCaptured
end

local function StopRawCombatLog(record)
  if not record then return end
  if record.rawCombatLogSession then
    record.rawCombatLogSession.stopRequestedAnchor = CaptureClockAnchor(record, "raw_log_stop_request")
  end
  if record.rawCombatLogOwned and type(LoggingCombat) == "function" then
    pcall(LoggingCombat, false)
    record.rawCombatLogOwned = false
  end
  if record.rawCombatLogSession and type(LoggingCombat) == "function" then
    local ok, enabled = pcall(LoggingCombat)
    record.rawCombatLogSession.enabledAfterStop = ok and enabled and true or false
    record.rawCombatLogSession.stoppedAnchor = CaptureClockAnchor(record, "raw_log_stop_complete")
  end
end

local function EnsureRawCombatLog(record, now)
  if not record or type(LoggingCombat) ~= "function" then return end
  now = now or Now()
  if now - (record.lastRawLogHealthAt or 0) < RAW_LOG_HEALTH_INTERVAL then return end
  record.lastRawLogHealthAt = now
  local ok, enabled = pcall(LoggingCombat)
  if ok and enabled then
    record.rawCombatLogCaptured = true
    return
  end
  local enableOk = pcall(LoggingCombat, true)
  local verifyOk, enabledAfter = pcall(LoggingCombat)
  local restart = {
    anchor = CaptureClockAnchor(record, "raw_log_restart"),
    success = enableOk and verifyOk and enabledAfter and true or false,
  }
  if record.rawCombatLogSession then
    local restarts = record.rawCombatLogSession.restarts or {}
    record.rawCombatLogSession.restarts = restarts
    if #restarts < 8 then restarts[#restarts + 1] = restart end
  end
  if enableOk and verifyOk and enabledAfter then
    record.rawCombatLogCaptured = true
    record.rawCombatLogRestarts = (record.rawCombatLogRestarts or 0) + 1
  else
    record.rawCombatLogFailures = (record.rawCombatLogFailures or 0) + 1
  end
end

-- The official WeakAuras profiler keeps its per-aura table private.  We do
-- not patch WeakAuras files to access it.  During an explicitly armed
-- diagnostic only, collect the finished official report into memory while
-- suppressing its profiler window, then restore every report method.
local function CaptureWeakAurasPrintedProfile()
  local aura = WeakAuras
  local popup = _G.WeakAurasProfilingReport
  if type(aura) ~= "table" or type(aura.PrintProfile) ~= "function" or type(popup) ~= "table" then
    return nil
  end
  local old = { AddText = popup.AddText, ClearText = popup.ClearText, ClearAllPoints = popup.ClearAllPoints, SetParent = popup.SetParent, SetPoint = popup.SetPoint, Show = popup.Show }
  if type(old.AddText) ~= "function" or type(old.Show) ~= "function" then return nil end
  local rows, systems, section = {}, {}, ""
  popup.ClearAllPoints = function() end
  popup.SetParent = function() end
  popup.SetPoint = function() end
  popup.ClearText = function() end
  popup.Show = function() end
  popup.AddText = function(_, text)
    local plain = tostring(text or ""):gsub("|c%x%x%x%x%x%x%x%x", ""):gsub("|r", "")
    if plain == "Auras:" then
      section = "auras"
    elseif plain == "Systems:" then
      section = "systems"
    elseif plain == "LibGetFrame:" then
      section = ""
    elseif section == "auras" then
      local name, elapsed = plain:match("^(.-)%s+([%d%.]+)ms")
      local spike = plain:match("%(([%d%.]+)ms%)")
      if name and elapsed then rows[#rows + 1] = { name = tostring(name), elapsed = tonumber(elapsed) or 0, spike = tonumber(spike) or 0, count = 0 } end
    elseif section == "systems" then
      local name, elapsed = plain:match("^(.-)%s+([%d%.]+)ms")
      local spike = plain:match("%(([%d%.]+)ms%)")
      if name and elapsed then systems[#systems + 1] = { name = tostring(name), elapsed = tonumber(elapsed) or 0, spike = tonumber(spike) or 0, count = 0 } end
    end
  end
  local ok = pcall(aura.PrintProfile)
  popup.AddText, popup.ClearText, popup.ClearAllPoints = old.AddText, old.ClearText, old.ClearAllPoints
  popup.SetParent, popup.SetPoint, popup.Show = old.SetParent, old.SetPoint, old.Show
  if not ok then return nil end
  table.sort(rows, function(a, b) return a.elapsed == b.elapsed and a.spike > b.spike or a.elapsed > b.elapsed end)
  table.sort(systems, function(a, b) return a.elapsed == b.elapsed and a.spike > b.spike or a.elapsed > b.elapsed end)
  while #rows > 80 do table.remove(rows) end
  while #systems > 80 do table.remove(systems) end
  return { source = "official_wa_profiler", auras = rows, systems = systems }
end

local function CallWeakAurasProfileSilently(methodName, ...)
  if not WeakAuras or type(WeakAuras[methodName]) ~= "function" then return false end
  local chat, original = DEFAULT_CHAT_FRAME, DEFAULT_CHAT_FRAME and DEFAULT_CHAT_FRAME.AddMessage
  if type(original) == "function" then
    -- StartProfile/StopProfile synchronously emit only their own status line.
    -- Mute that tiny call boundary instead of guessing localized message text.
    chat.AddMessage = function() end
  end
  local ok = pcall(WeakAuras[methodName], ...)
  if type(original) == "function" then chat.AddMessage = original end
  return ok
end

local function CaptureWeakAurasProfile()
  if not WeakAuras or type(WeakAuras.GetRaidOptimizerProfileSnapshot) ~= "function" then
    return CaptureWeakAurasPrintedProfile()
  end
  local ok, snapshot = pcall(WeakAuras.GetRaidOptimizerProfileSnapshot)
  if not ok or type(snapshot) ~= "table" then return CaptureWeakAurasPrintedProfile() end
  local function TopRows(source, limit)
    local rows = {}
    for name, value in pairs(source or {}) do
      if type(value) == "table" and name ~= "time" then
        rows[#rows + 1] = {
          name = tostring(name),
          elapsed = tonumber(value.elapsed) or 0,
          spike = tonumber(value.spike) or 0,
          count = tonumber(value.count) or 0,
        }
      end
    end
    table.sort(rows, function(a, b)
      if a.elapsed == b.elapsed then return a.spike > b.spike end
      return a.elapsed > b.elapsed
    end)
    while #rows > limit do table.remove(rows) end
    return rows
  end
  local total = snapshot.systems and snapshot.systems.time or nil
  local wa = snapshot.systems and snapshot.systems.wa or nil
  return {
    elapsed = total and (tonumber(total.elapsed) or 0) or 0,
    waElapsed = wa and (tonumber(wa.elapsed) or 0) or 0,
    systems = TopRows(snapshot.systems, 40),
    auras = TopRows(snapshot.auras, 80),
    -- Read-only end-of-window fan-out. This adds no per-frame hooks: the
    -- patched WA exposes current animations, clones and FrameTick subscribers
    -- only when the recorder asks for its final snapshot.
    displayWork = snapshot.displayWork,
  }
end

local function CaptureWeakAurasDisplayWork(record)
  if not record
    or not WeakAuras
    or type(WeakAuras.GetRaidOptimizerDisplayWorkSnapshot) ~= "function"
  then
    return
  end
  local ok, snapshot = pcall(WeakAuras.GetRaidOptimizerDisplayWorkSnapshot)
  if not ok or type(snapshot) ~= "table" then return end
  local peak = record.waDisplayWorkPeak
  if type(peak) ~= "table" then
    peak = {
      samples = 0,
      activeAnimations = 0,
      loopingAnimations = 0,
      regions = 0,
      visibleRegions = 0,
      cloneRegions = 0,
      frameTickRegions = 0,
      byAura = {},
    }
    record.waDisplayWorkPeak = peak
    record.waDisplayWorkBaseline = {
      knownCustomCleuRejects = tonumber(snapshot.knownCustomCleuRejects) or 0,
      sourcePlayerRejects = tonumber(snapshot.sourcePlayerRejects) or 0,
      plannedDisplays = tonumber(snapshot.plannedDisplays) or 0,
      nativeDisplays = tonumber(snapshot.nativeDisplays) or 0,
      swingTimerBindings = tonumber(snapshot.swingTimerBindings) or 0,
      statusPlayerAuraBindings = tonumber(snapshot.statusPlayerAuraBindings) or 0,
    }
  end
  peak.samples = (peak.samples or 0) + 1
  local animations = snapshot.animationSnapshot or {}
  local regions = snapshot.regionSnapshot or {}
  peak.activeAnimations = math.max(peak.activeAnimations or 0, tonumber(animations.active) or 0)
  peak.loopingAnimations = math.max(peak.loopingAnimations or 0, tonumber(animations.looping) or 0)
  peak.regions = math.max(peak.regions or 0, tonumber(regions.regions) or 0)
  peak.visibleRegions = math.max(peak.visibleRegions or 0, tonumber(regions.visibleRegions) or 0)
  peak.cloneRegions = math.max(peak.cloneRegions or 0, tonumber(regions.cloneRegions) or 0)
  peak.frameTickRegions = math.max(peak.frameTickRegions or 0, tonumber(regions.frameTickRegions) or 0)
  local function MergeAura(source, kind)
    for id, row in pairs(source or {}) do
      if type(row) == "table" then
        local target = peak.byAura[id]
        if not target then
          target = { id = tostring(id) }
          peak.byAura[id] = target
        end
        if kind == "animation" then
          target.activeAnimations = math.max(
            target.activeAnimations or 0, tonumber(row.active) or 0)
          target.loopingAnimations = math.max(
            target.loopingAnimations or 0, tonumber(row.looping) or 0)
          target.animationClones = math.max(
            target.animationClones or 0, tonumber(row.clones) or 0)
        else
          target.regions = math.max(target.regions or 0, tonumber(row.regions) or 0)
          target.visibleRegions = math.max(
            target.visibleRegions or 0, tonumber(row.visibleRegions) or 0)
          target.clones = math.max(target.clones or 0, tonumber(row.clones) or 0)
          target.frameTickRegions = math.max(
            target.frameTickRegions or 0, tonumber(row.frameTickRegions) or 0)
        end
      end
    end
  end
  MergeAura(animations.byAura, "animation")
  MergeAura(regions.byAura, "region")
  local baseline = record.waDisplayWorkBaseline or {}
  peak.knownCustomCleuRejects = math.max(
    peak.knownCustomCleuRejects or 0,
    (tonumber(snapshot.knownCustomCleuRejects) or 0)
      - (tonumber(baseline.knownCustomCleuRejects) or 0))
  peak.sourcePlayerRejects = math.max(
    peak.sourcePlayerRejects or 0,
    (tonumber(snapshot.sourcePlayerRejects) or 0)
      - (tonumber(baseline.sourcePlayerRejects) or 0))
  peak.plannedDisplays = math.max(
    peak.plannedDisplays or 0,
    (tonumber(snapshot.plannedDisplays) or 0)
      - (tonumber(baseline.plannedDisplays) or 0))
  peak.nativeDisplays = math.max(
    peak.nativeDisplays or 0,
    (tonumber(snapshot.nativeDisplays) or 0)
      - (tonumber(baseline.nativeDisplays) or 0))
end

local function StartRaidRecord(deepCpu, waProfileCapture, startOptions)
  if not db then return end
  if recorderActive then
    -- Native recording commonly starts itself as the raid loads.  A manual
    -- WA diagnostic must attach to that safe record instead of being
    -- discarded as a duplicate start request.
    if waProfileCapture and db.raidRecord and not db.raidRecord.waProfileCapture then
      db.raidRecord.waProfileCapture = true
      db.raidRecord.waProfileMode = "manual_boss_window"
      db.raidRecord.waProfileAttachedAt = Now()
      db.raidRecord.waProfileStarted = CallWeakAurasProfileSilently("StartProfile")
      CaptureWeakAurasDisplayWork(db.raidRecord)
      Print("WA归因已附加到当前记录：请在本场Boss结束后输入 /rops stop。")
      return
    end
    Print("轻量记录已经在运行，不需要重复开始。")
    return
  end
  startOptions = type(startOptions) == "table" and startOptions or {}
  deepCpu = deepCpu and true or false
  waProfileCapture = waProfileCapture and true or false
  local nativeOnly = startOptions.nativeOnly ~= false and not deepCpu
  if not nativeOnly then HookLuaErrorHandler() end
  if deepCpu and not CpuProfileEnabled() then
    EnableCpuProfile()
    AddLine("旧性能记录开关只用于内部排查，客户版不开放。")
    return
  end
  if not deepCpu and CpuProfileEnabled() then
    DisableCpuProfile()
    AddLine("检测到旧性能记录开关仍处于开启状态。\n\n为了避免影响帧数，已尝试关闭。\n请先 /reload 或重启游戏，再点“开始记录”。")
    return
  end
  waScanEvents = 0
  combatLogEvents = 0
  StopRobotProbe()
  robotProbeCalls, robotProbeMs, robotProbeMaxMs = 0, 0, 0
  if not nativeOnly then InstallDetailsHooks() end
  local lgf = not nativeOnly and GetLibGetFrameStats() or nil
  local startCpuTotal, startCpuRows = 0, {}
  if deepCpu then
    startCpuTotal, startCpuRows = GetAddonCPU()
  end
  local startedMonotonicSec, monotonicSource = PreciseNow()
  db.raidRecord = {
    active = true,
    schemaRevision = 4,
    nativeOnly = nativeOnly,
    safeMode = not deepCpu,
    deepCpu = deepCpu,
    waProfileCapture = waProfileCapture,
    autoStopOnEncounterEnd = startOptions.autoStopOnEncounterEnd and true or false,
    autoStarted = startOptions.autoStarted and true or false,
    startReason = tostring(startOptions.reason or "manual"),
    continuationOf = startOptions.continuationOf,
    runId = tostring(date("%Y%m%d-%H%M%S")) .. "-" .. tostring(math.floor(Now() * 1000)),
    deepEvents = false,
    startedAt = Now(),
    startedMonotonicSec = startedMonotonicSec,
    monotonicSource = monotonicSource,
    startedUnixSec = UnixNow(),
    startedServerUnixSec = ServerUnixNow(),
    startedAtText = date("%Y-%m-%d %H:%M:%S"),
    updatedAtText = date("%Y-%m-%d %H:%M:%S"),
    samples = {},
    events = {},
    sampleCount = 0,
    fpsSampleCount = 0,
    fpsSum = 0,
    totalFrames = 0,
    totalFrameMs = 0,
    samplesDropped = 0,
    minFPS = 9999,
    maxCpuDelta = 0,
    maxWAEvents = 0,
    maxCombatLogs = 0,
    maxLuaErrors = 0,
    maxLgfQueries = 0,
    lastCpuTotal = startCpuTotal,
    lastCpuRows = startCpuRows,
    lastWAEvents = 0,
    lastCombatLogEvents = 0,
    lastLgfQueries = lgf and lgf.queries or 0,
    lastLogCategories = {},
    lastLogScopes = {},
    logCategories = {},
    logScopes = {},
    logSubevents = {},
    luaErrorCount = 0,
    lastLuaErrorCount = 0,
    luaErrorAddons = {},
    luaErrorMessages = {},
    luaErrorRecent = {},
    cpuProfile = deepCpu,
    spellTotals = {},
    cpuTotals = {},
    cpuPeaks = {},
    bossSegments = {},
    currentBossIndex = nil,
    maxFrameMs = 0,
    over33Frames = 0,
    over50Frames = 0,
    over100Frames = 0,
    maxCombatLogsPerSecond = 0,
    frameWindowFrames = 0,
    frameWindowMs = 0,
    frameWindowWorstMs = 0,
    frameWindowOver33 = 0,
    frameWindowOver50 = 0,
    frameWindowOver100 = 0,
    lastDetailsRefreshCalls = detailsRefreshCalls,
    lastDetailsRowRefreshCalls = detailsRowRefreshCalls,
    lastRobotProbeCalls = 0,
    lastRobotProbeMs = 0,
    lastNameplateAdds = nameplateAdds,
    lastNameplateRemoves = nameplateRemoves,
    maxHubMeasuredMs = 0,
    maxHubEstimatedMs = 0,
    maxHubEstimatedUpperMs = 0,
    maxWAConsumerMs = 0,
    maxAuraRefreshMs = 0,
    maxRobotMs = 0,
    maxRobotCallMs = 0,
    maxDetailsRefreshes = 0,
    maxDetailsRowRefreshes = 0,
    maxLuaGrowthKB = 0,
    totalGcDrops = 0,
    attributionWindows = 0,
    attributionReasons = {},
    nextAttributionWindowAt = 0,
    nextPeriodicAttributionAt = Now() + 30,
    recorderSampleMs = 0,
    maxRecorderSampleMs = 0,
    elapsedSec = 0,
    nativeSlowFrames = {},
    nativeSlowFramesDropped = 0,
    cpuBoundSamples = { cpu = 0, notCpu = 0, unknown = 0 },
    clockAnchors = {},
    clockAnchorCount = 0,
    sceneMode = startOptions.sceneMode and true or false,
    sceneSegments = {},
    currentSceneSegment = nil,
  }
  db.raidRecord.startClockAnchor = CaptureClockAnchor(db.raidRecord, "run_start")
  db.raidRecord.candidateIdentityStart = CaptureCandidateIdentity and CaptureCandidateIdentity() or nil
  db.raidRecord.nativeProfilerCapability = ProbeNativeProfiler()
  NativeProfilerRecentStats(db.raidRecord)
  if not nativeOnly then
    local hubBaseline = GetHubFlightCounters()
    if hubBaseline then
      db.raidRecord.lastHubFlight = CopyHubFlightCounters(hubBaseline)
    end
  end
  recorderActive = true
  if waProfileCapture then
    -- Start/stop is deliberately manual and synchronous. WeakAuras' delayed
    -- next-encounter mode emits its own later chat messages, while this path
    -- remains completely silent and is only used for one Boss-sized window.
    db.raidRecord.waProfileMode = "manual_boss_window"
    db.raidRecord.waProfileStarted = CallWeakAurasProfileSilently("StartProfile")
    CaptureWeakAurasDisplayWork(db.raidRecord)
  end
  if not db.raidRecord.sceneMode then
    StartRawCombatLog(db.raidRecord)
    db.raidRecord.lastRawLogHealthAt = Now()
  end
  if not nativeOnly then
    if not StartHubRate(db.raidRecord) then
      recorderFrame:RegisterEvent("COMBAT_LOG_EVENT_UNFILTERED")
    end
  end
  lastRecorderSample = nativeOnly and 0 or Now()
  recorderFrame:SetScript("OnUpdate", function(_, frameElapsed)
    if not recorderActive then return end
    local record = db.raidRecord
    AccumulateFrameWindow(record, frameElapsed)
    if record.nativeOnly then
      if (record.elapsedSec or 0) - lastRecorderSample >= RECORD_SAMPLE_INTERVAL then
        lastRecorderSample = record.elapsedSec or 0
        if not record.sceneMode then EnsureRawCombatLog(record, Now()) end
        local snapshot = RecordSnapshot("tick")
        if record.waProfileCapture then CaptureWeakAurasDisplayWork(record) end
        -- Bind the profiler evidence to a real hitch, not merely to the
        -- encounter as a whole.  The three-second guard prevents the
        -- diagnostic itself from becoming a continuous CPU monitor.
        if snapshot and (tonumber(snapshot.worstFrameMs) or 0) >= NATIVE_PROFILER_SPIKE_FRAME_MS then
          CaptureRecentProfilerSample(record, "slow_frame", false, snapshot)
          RaidOptimizerCaptureSceneProfilerSample(record, record.currentSceneSegment, "slow_frame", false, snapshot)
        end
        CaptureRecentProfilerSample(record, "interval", false)
        RaidOptimizerCaptureSceneProfilerSample(record, record.currentSceneSegment, "interval", false)
        if RefreshMiniStatus then RefreshMiniStatus() end
      end
      return
    end
    local now = Now()
    if now - (record.lastAttributionMaintenanceAt or 0) >= 0.25 then
      record.lastAttributionMaintenanceAt = now
      MaintainAttributionWindow(record, now)
    end
    if now - lastRecorderSample >= RECORD_SAMPLE_INTERVAL then
      lastRecorderSample = now
      EnsureRawCombatLog(db.raidRecord, now)
      InstallDetailsHooks()
      RecordSnapshot("tick")
      if record.waProfileCapture then CaptureWeakAurasDisplayWork(record) end
      if panel and panel:IsShown() then
        AddLine(string.format(
          "轻量记录中...\n时长：%.1f 分钟\n最低 FPS：%.1f\n最长单帧：%.1fms\n事件峰值：%d/秒\nCPU统计：关闭\n原始日志：%s",
          (now - (db.raidRecord.startedAt or now)) / 60,
          db.raidRecord.minFPS == 9999 and 0 or (db.raidRecord.minFPS or 0),
          db.raidRecord.maxFrameMs or 0,
          db.raidRecord.maxCombatLogsPerSecond or db.raidRecord.maxCombatLogs or 0,
          db.raidRecord.rawCombatLogCaptured and "持续写盘" or "未开启"
        ))
      end
      if RefreshMiniStatus then RefreshMiniStatus() end
    end
  end)
  if not nativeOnly then ArmAttributionWindow(db.raidRecord, "record_start", 3, true) end
  if RefreshMiniStatus then RefreshMiniStatus() end
  if waProfileCapture then
    Print("WA归因已开始：请只覆盖一场Boss后输入 /rops stop；本轮不用于比较FPS。")
  elseif deepCpu then
    Print("内部归因记录已开始。")
  else
    if db.raidRecord.sceneMode then
      Print("场景帧数体检已开始：记录主城/副本分段、慢帧和暴雪原生插件耗时；不写战斗日志。")
    else
      Print("原生黑匣子已开始：只记录帧时间、Boss锚点和暴雪原生插件耗时。")
    end
  end
end

local function FinalizeRaidRecord(reason)
  if not db or not db.raidRecord then
    return false, "没有实战记录。"
  end
  if db.raidRecord.active then
    local record = db.raidRecord
    local hasSamples = record.samples and #record.samples > 0
    if (record.frameWindowMs or 0) >= MIN_FPS_WINDOW_MS or not hasSamples then
      RecordSnapshot(reason or "stop")
    end
    db.raidRecord.active = false
  end
  local record = db.raidRecord
  if record.currentSceneSegment then
    record.currentSceneSegment.endAt = record.elapsedSec or record.currentSceneSegment.startAt or 0
    record.currentSceneSegment = nil
  end
  local activeBoss = CurrentBossSegment(record)
  if activeBoss then
    if record.nativeOnly then
      CaptureRecentProfilerSample(record, "boss_incomplete_end", true)
    end
    activeBoss.active = false
    activeBoss.incomplete = true
    activeBoss.endAt = record.elapsedSec or activeBoss.startAt or 0
    activeBoss.endT = math.floor(activeBoss.endAt)
    activeBoss.endClockAnchor = CaptureClockAnchor(record, "encounter_incomplete:" .. tostring(activeBoss.encounterId or 0))
    activeBoss.candidateIdentityEnd = CaptureCandidateIdentity and CaptureCandidateIdentity() or nil
    local hubEnd = GetHubFlightCounters and GetHubFlightCounters() or nil
    if hubEnd and CopyHubFlightCounters then
      activeBoss.hubFlightEnd = CopyHubFlightCounters(hubEnd)
      if BuildHubFlightDelta then
        activeBoss.candidateRouteDelta = BuildHubFlightDelta(
          activeBoss.hubFlightStart, activeBoss.hubFlightEnd)
      end
    end
    activeBoss.candidateTelemetryAvailable = type(activeBoss.candidateRouteDelta) == "table"
      and type(activeBoss.candidateIdentityEnd) == "table"
    record.currentBossIndex = nil
  end
  if record.nativeOnly then
    for _, boss in ipairs(record.bossSegments or {}) do
      if boss.endAt ~= nil and not boss.nativeProfilerFinalized then
        boss.nativeProfilerFinalized = true
        boss.nativeProfilerEnd = CaptureNativeProfilerSnapshot(record, "end")
        FinalizeNativeProfilerBoss(boss)
      end
    end
    record.endClockAnchor = CaptureClockAnchor(record, "run_end:" .. tostring(reason or "stop"))
  end
  db.raidRecord.endedAtText = db.raidRecord.endedAtText or date("%Y-%m-%d %H:%M:%S")
  db.raidRecord.candidateIdentityEnd = CaptureCandidateIdentity and CaptureCandidateIdentity() or nil
  if recorderActive then
    recorderActive = false
    recorderFrame:UnregisterEvent("COMBAT_LOG_EVENT_UNFILTERED")
    recorderFrame:SetScript("OnUpdate", nil)
  end
  StopRobotProbe()
  StopHubRate()
  if not db.raidRecord.sceneMode then StopRawCombatLog(db.raidRecord) end
  if db.raidRecord.waProfileCapture then
    CaptureWeakAurasDisplayWork(db.raidRecord)
    db.raidRecord.waProfileStopped = CallWeakAurasProfileSilently("StopProfile")
    db.raidRecord.waProfile = CaptureWeakAurasProfile()
    if type(db.raidRecord.waProfile) == "table" then
      db.raidRecord.waProfile.displayWorkPeak = db.raidRecord.waDisplayWorkPeak
    end
  end
  local text = SummarizeRaidRecord(db.raidRecord, false)
  local fullText = SummarizeRaidRecord(db.raidRecord, true)
  db.latestRaidReport = text
  db.latestRaidReportFull = fullText
  db.latestRaidExport = BuildRaidExport(db.raidRecord, text)
  local bossExport = BuildBossDiagnosticExport(db.latestRaidExport)
  if bossExport then db.latestBossRaidExport = bossExport end
  db.raidHistory = db.raidHistory or {}
  if db.latestRaidExport and not db.raidRecord.exportArchived then
    table.insert(db.raidHistory, 1, BuildRaidHistorySummary(db.latestRaidExport))
    while #db.raidHistory > 4 do table.remove(db.raidHistory) end
    db.raidRecord.exportArchived = true
  end
  db.latestRaidSavedAt = date("%Y-%m-%d %H:%M:%S")
  -- The full timeline lives only in latestRaidExport. WoW's SavedVariables
  -- serializer expands shared references, so retaining it here and in history
  -- would write the same multi-megabyte sample tree three times.
  db.raidRecord = nil
  return true, text
end

local function StopRaidRecord()
  local ok, text = FinalizeRaidRecord("stop")
  if not ok then
    AddLine(text or "没有实战记录。")
    return
  end
  AddLine(text)
  if RefreshMiniStatus then RefreshMiniStatus() end
  Print("流畅度记录已结束，报告已生成。请手动输入 /reload 或正常退出游戏完成落盘。")
  AddLine(text .. "\n\n保存状态：报告已生成。为避免插件触发受保护界面操作，不再自动重载；请手动输入 /reload，重载完成后可点“查看报告”。")
end

-- Arm exactly one future Boss window for the official WeakAuras profiler.
-- This is deliberately separate from the low-overhead FPS recorder: the
-- profiler run finds causes, while the ordinary run measures the result.
function RaidOptimizerArmWeakAurasBossProfile()
  if not db then return end
  if recorderActive and db.raidRecord and db.raidRecord.waProfileCapture then
    Print("WA深度归因正在记录；本场Boss结束后会自动保存，不需要宏命令。")
    return
  end
  db.waProfileArm = {
    armed = true,
    armedAt = Now(),
    waitForCurrentBoss = recorderActive and CurrentBossSegment(db.raidRecord) ~= nil or false,
  }
  if recorderActive and not db.waProfileArm.waitForCurrentBoss then
    FinalizeRaidRecord("switch_to_wa_profile_arm")
  end
  if db.waProfileArm.waitForCurrentBoss then
    Print("WA深度归因已武装：当前Boss仍按低开销模式记录，下一场Boss自动归因并保存。")
  else
    Print("WA深度归因已武装：下一场Boss自动开始、结束并保存，不需要宏命令。")
  end
  if panel and panel:IsShown() then
    AddLine("WA深度归因已武装。下一场Boss自动启动官方WA Profiler，Boss结束后自动停止并保存；本轮不计入FPS成绩。")
  end
end

local function IsRaidInstance()
  if type(IsInInstance) ~= "function" then return false end
  local ok, inInstance, instanceType = pcall(IsInInstance)
  return ok and inInstance and instanceType == "raid"
end

-- Classic five-player instances report "party" while raids report "raid".
-- Both are real dungeon evidence and should be captured without requiring a
-- slash command; battlegrounds and the outdoor world remain excluded.
function RaidOptimizerIsDiagnosticInstance()
  if type(IsInInstance) ~= "function" then return false end
  local ok, inInstance, instanceType = pcall(IsInInstance)
  return ok and inInstance and (instanceType == "raid" or instanceType == "party" or instanceType == "scenario")
end

local function ShouldAutoRecordEncounter()
  if IsRaidInstance() then return true end
  if type(GetNumGroupMembers) == "function" then
    local ok, members = pcall(GetNumGroupMembers)
    if ok and (tonumber(members) or 0) > 5 then return true end
  end
  return false
end

local function ArchiveInterruptedRecord()
  if not db or not db.raidRecord or not db.raidRecord.active then return nil end
  local old = db.raidRecord
  old.active = false
  old.interrupted = true
  old.endedAtText = old.endedAtText or date("%Y-%m-%d %H:%M:%S")
  old.updatedAtText = date("%Y-%m-%d %H:%M:%S")
  db.latestRaidReport = SummarizeRaidRecord(old, false)
  db.latestRaidReportFull = SummarizeRaidRecord(old, true)
  db.latestRaidExport = BuildRaidExport(old, db.latestRaidReport)
  local bossExport = BuildBossDiagnosticExport(db.latestRaidExport)
  if bossExport then db.latestBossRaidExport = bossExport end
  db.raidHistory = db.raidHistory or {}
  if db.latestRaidExport and not old.exportArchived then
    table.insert(db.raidHistory, 1, BuildRaidHistorySummary(db.latestRaidExport))
    while #db.raidHistory > 4 do table.remove(db.raidHistory) end
    old.exportArchived = true
  end
  local runId = old.runId
  db.raidRecord = nil
  return runId
end

local function StartAutomaticRaidRecord(reason)
  if recorderActive then return true end
  if not db then return false end
  StartRaidRecord(false, false, {
    autoStarted = true,
    nativeOnly = true,
    reason = reason or "raid_enter",
    continuationOf = pendingContinuationOf,
  })
  if recorderActive then
    pendingContinuationOf = nil
    AddRecordEvent("自动记录", tostring(reason or "进入团队副本"))
    return true
  end
  return false
end

local function ScheduleAutoRaidReconcile(reason, delaySeconds)
  autoRaidCheckGeneration = autoRaidCheckGeneration + 1
  local generation = autoRaidCheckGeneration
  local function Check()
    if generation ~= autoRaidCheckGeneration then return end
    if RaidOptimizerIsDiagnosticInstance() then
      StartAutomaticRaidRecord(reason or "instance_enter")
      return
    end
    if not recorderActive or not db or not db.raidRecord or not db.raidRecord.autoStarted then return end
    local function ConfirmExit()
      if generation ~= autoRaidCheckGeneration or RaidOptimizerIsDiagnosticInstance() then return end
      if recorderActive and db and db.raidRecord and db.raidRecord.autoStarted then
        AddRecordEvent("自动结束", "离开副本")
        FinalizeRaidRecord("instance_exit")
      end
    end
    if C_Timer and type(C_Timer.After) == "function" then
      C_Timer.After(AUTO_RAID_EXIT_GRACE, ConfirmExit)
    else
      ConfirmExit()
    end
  end
  if C_Timer and type(C_Timer.After) == "function" and (delaySeconds or 0) > 0 then
    C_Timer.After(delaySeconds, Check)
  else
    Check()
  end
end

local function ShowRaidRecord()
  if db and db.raidRecord then
    AddLine(SummarizeRaidRecord(db.raidRecord, false))
  elseif db and db.latestRaidReport then
    AddLine(db.latestRaidReport)
  else
    AddLine("还没有实战记录。打本前点“开始记录”，整场打完点“结束保存”。")
  end
end

local function ShowRaidRecordFull()
  if db and db.raidRecord then
    AddLine(SummarizeRaidRecord(db.raidRecord, true))
  elseif db and db.latestRaidReportFull then
    AddLine(db.latestRaidReportFull)
  elseif db and db.latestRaidReport then
    AddLine(db.latestRaidReport)
  else
    AddLine("还没有实战记录。打本前点“开始记录”，整场打完点“结束保存”。")
  end
end

local function FinishTest()
  if not activeTest then return end
  local test = activeTest
  activeTest = nil
  frame:SetScript("OnUpdate", nil)

  local endCPU, endRows = GetAddonCPU()
  local endMemory, memoryRows = GetAddonMemory()
  local lgfStats = GetLibGetFrameStats()
  local avg = test.samples > 0 and (test.fpsSum / test.samples) or 0
  local minFPS = test.minFPS == 9999 and 0 or test.minFPS
  local duration = Now() - test.started
  local report = {
    mode = test.mode,
    label = ModeLabel(test.mode),
    time = date("%Y-%m-%d %H:%M:%S"),
    duration = duration,
    samples = test.samples,
    avgFPS = avg,
    minFPS = minFPS,
    stutters = test.stutters,
    waEvents = test.waEvents,
    nameplateReads = test.nameplateReads,
    visibleNameplates = CountVisibleNameplates(),
    addonCPUDelta = endCPU - test.startCPU,
    addonMemoryMB = endMemory / 1024,
    cpuProfile = CpuProfileEnabled(),
  }
  db.history = db.history or {}
  table.insert(db.history, 1, report)
  while #db.history > 12 do table.remove(db.history) end
  db.latest = report

  local text = string.format(
    "%s 完成\n==============================\n时长：%.1f 秒\n平均 FPS：%.1f\n最低 FPS：%.1f\n卡顿次数：%d\nWA/事件触发：%d\n姓名板读取：%d\n当前可见姓名板：%d\n%s",
    report.label,
    duration,
    avg,
    minFPS,
    report.stutters,
    report.waEvents,
    report.nameplateReads,
    report.visibleNameplates,
    FormatLibGetFrameStats(lgfStats)
  )
  AddLine(text)
  Print(report.label .. "完成，平均FPS " .. string.format("%.1f", avg) .. "，最低FPS " .. string.format("%.1f", minFPS) .. "。")
end

local function StartTest(mode)
  if activeTest then
    Print("已有测试正在运行，请等它结束。")
    return
  end
  local startCPU, startRows = GetAddonCPU()
  activeTest = {
    mode = mode,
    started = Now(),
    lastSample = 0,
    fpsSum = 0,
    minFPS = 9999,
    samples = 0,
    stutters = 0,
    waEvents = 0,
    nameplateReads = 0,
    startCPU = startCPU,
    startRows = startRows,
  }
  AddLine(ModeLabel(mode) .. "运行中...\n请保持同一场景，不要切换窗口。\n预计 " .. TEST_DURATION .. " 秒。")
  Print(ModeLabel(mode) .. "开始，持续 " .. TEST_DURATION .. " 秒。")
  frame:SetScript("OnUpdate", function(_, elapsed)
    local test = activeTest
    if not test then return end
    local now = Now()
    if now - test.started >= TEST_DURATION then
      FinishTest()
      return
    end
    if mode == "wa" or mode == "combined" then
      test.waEvents = test.waEvents + FireWAEvents()
    end
    if mode == "nameplate" or mode == "combined" then
      test.nameplateReads = test.nameplateReads + NameplateScanWork()
    end
    if now - test.lastSample >= SAMPLE_INTERVAL then
      test.lastSample = now
      local fps = GetFramerate and GetFramerate() or 0
      test.samples = test.samples + 1
      test.fpsSum = test.fpsSum + fps
      if fps < test.minFPS then test.minFPS = fps end
      if fps > 0 and fps < 45 then test.stutters = test.stutters + 1 end
      local remaining = math.max(0, TEST_DURATION - (now - test.started))
      AddLine(string.format("%s运行中...\n剩余：%.0f 秒\n当前 FPS：%.1f\n最低 FPS：%.1f\nWA/事件触发：%d\n姓名板读取：%d", ModeLabel(mode), remaining, fps, test.minFPS == 9999 and fps or test.minFPS, test.waEvents, test.nameplateReads))
    end
  end)
end

local function LastReportText()
  local latest = db and db.latest
  if not latest then
    return "还没有记录报告。\n\n打本前点“开始记录”，整场打完点“结束保存”，再点“查看报告”。"
  end
  return string.format(
    "最近报告：%s\n==============================\n时间：%s\n平均 FPS：%.1f\n最低 FPS：%.1f\n卡顿次数：%d\nWA/事件触发：%d\n姓名板读取：%d\n可见姓名板：%d",
    latest.label or latest.mode,
    latest.time or "",
    latest.avgFPS or 0,
    latest.minFPS or 0,
    latest.stutters or 0,
    latest.waEvents or 0,
    latest.nameplateReads or 0,
    latest.visibleNameplates or 0
  )
end

local function CreateButton(parent, text, x, y, w, onClick)
  local button = CreateFrame("Button", nil, parent, "UIPanelButtonTemplate")
  button:SetSize(w or 110, 24)
  button:SetPoint("TOPLEFT", x, y)
  button:SetText(text)
  button:SetScript("OnClick", onClick)
  return button
end

local function CreatePanel()
  if panel then return end
  panel = CreateFrame("Frame", "RaidOptimizerStressTestPanel", UIParent, BACKDROP_TEMPLATE)
  panel:SetSize(620, 430)
  panel:SetPoint("CENTER")
  panel:SetMovable(true)
  panel:EnableMouse(true)
  panel:RegisterForDrag("LeftButton")
  panel:SetScript("OnDragStart", panel.StartMoving)
  panel:SetScript("OnDragStop", panel.StopMovingOrSizing)
  if panel.SetBackdrop then
    panel:SetBackdrop({
      bgFile = "Interface\\Tooltips\\UI-Tooltip-Background",
      edgeFile = "Interface\\Tooltips\\UI-Tooltip-Border",
      tile = true, tileSize = 16, edgeSize = 12,
      insets = { left = 3, right = 3, top = 3, bottom = 3 },
    })
    panel:SetBackdropColor(0.04, 0.06, 0.07, 0.94)
    panel:SetBackdropBorderColor(0.75, 0.62, 0.35, 0.9)
  end
  local title = panel:CreateFontString(nil, "OVERLAY", "GameFontNormalLarge")
  title:SetPoint("TOPLEFT", 14, -12)
  title:SetText("|cffffd36b团本优化大师|r 流畅度诊断")
  local note = panel:CreateFontString(nil, "OVERLAY", "GameFontDisableSmall")
  note:SetPoint("TOPLEFT", 14, -38)
  note:SetText("进入五人或团队副本会自动低开销记录；逐Boss保存，不需要宏命令。")

  CreateButton(panel, "开始记录", 14, -66, 112, function() StartRaidRecord(false) end)
  CreateButton(panel, "结束保存", 134, -66, 112, StopRaidRecord)
  CreateButton(panel, "查看报告", 254, -66, 112, ShowRaidRecord)
  CreateButton(panel, "完整报告", 374, -66, 112, ShowRaidRecordFull)
  CreateButton(panel, "最近报告", 14, -96, 112, function() AddLine(LastReportText()) end)
  CreateButton(panel, "WA深度归因", 134, -96, 112, RaidOptimizerArmWeakAurasBossProfile)
  CreateButton(panel, "关闭", 494, -66, 112, function() panel:Hide() end)

  panel.scroll = CreateFrame("ScrollFrame", "RaidOptimizerStressTestScrollFrame", panel, "UIPanelScrollFrameTemplate")
  panel.scroll:SetPoint("TOPLEFT", 16, -128)
  panel.scroll:SetPoint("BOTTOMRIGHT", -30, 16)
  panel.scrollChild = CreateFrame("Frame", nil, panel.scroll)
  panel.scrollChild:SetSize(560, 900)
  panel.scroll:SetScrollChild(panel.scrollChild)

  panel.output = panel.scrollChild:CreateFontString(nil, "OVERLAY", "GameFontHighlightSmall")
  panel.output:SetPoint("TOPLEFT", panel.scrollChild, "TOPLEFT", 0, 0)
  panel.output:SetWidth(560)
  panel.output:SetJustifyH("LEFT")
  panel.output:SetJustifyV("TOP")
  panel.output:SetText(LastReportText())
  panel:Hide()
end

local function TogglePanel()
  CreatePanel()
  if panel:IsShown() then
    panel:Hide()
  else
    panel:Show()
    AddLine(LastReportText())
  end
end

local function OpenPanelWithReport()
  CreatePanel()
  panel:Show()
  ShowRaidRecord()
end

RefreshMiniStatus = function()
  if not miniPanel or not miniPanel.status then return end
  if recorderActive and db and db.raidRecord then
    local elapsed = math.max(0, Now() - (db.raidRecord.startedAt or Now()))
    miniPanel.status:SetText(string.format("|cff66ff99记录中|r  %.0f 分钟  最低FPS %.1f", elapsed / 60, db.raidRecord.minFPS == 9999 and 0 or db.raidRecord.minFPS))
  elseif db and (db.latestRaidReport or db.raidRecord) then
    miniPanel.status:SetText("|cffffd36b已停止|r  可点“查看报告”")
  else
    miniPanel.status:SetText("|cffaaaaaa待命|r  进副本后自动开始")
  end
end

local function CreateMiniPanel()
  if miniPanel then return end
  miniPanel = CreateFrame("Frame", "RaidOptimizerStressMiniPanel", UIParent, BACKDROP_TEMPLATE)
  miniPanel:SetSize(318, 138)
  miniPanel:SetPoint("CENTER", UIParent, "CENTER", 0, 160)
  miniPanel:SetMovable(true)
  miniPanel:EnableMouse(true)
  miniPanel:RegisterForDrag("LeftButton")
  miniPanel:SetScript("OnDragStart", miniPanel.StartMoving)
  miniPanel:SetScript("OnDragStop", function(self)
    self:StopMovingOrSizing()
  end)
  if miniPanel.SetBackdrop then
    miniPanel:SetBackdrop({
      bgFile = "Interface\\Tooltips\\UI-Tooltip-Background",
      edgeFile = "Interface\\Tooltips\\UI-Tooltip-Border",
      tile = true, tileSize = 16, edgeSize = 12,
      insets = { left = 3, right = 3, top = 3, bottom = 3 },
    })
    miniPanel:SetBackdropColor(0.04, 0.06, 0.07, 0.92)
    miniPanel:SetBackdropBorderColor(0.75, 0.62, 0.35, 0.9)
  end

  local title = miniPanel:CreateFontString(nil, "OVERLAY", "GameFontNormal")
  title:SetPoint("TOPLEFT", 10, -9)
  title:SetText("|cffffd36b团本优化大师|r 流畅度诊断")

  local close = CreateFrame("Button", nil, miniPanel, "UIPanelCloseButton")
  close:SetPoint("TOPRIGHT", 3, 3)
  close:SetScript("OnClick", function()
    if db then db.miniHidden = true end
    miniPanel:Hide()
    Print("流畅度诊断小面板已隐藏，输入 /rops mini 可再次打开。")
  end)

  miniPanel.status = miniPanel:CreateFontString(nil, "OVERLAY", "GameFontHighlightSmall")
  miniPanel.status:SetPoint("TOPLEFT", 10, -32)
  miniPanel.status:SetWidth(292)
  miniPanel.status:SetJustifyH("LEFT")

  CreateButton(miniPanel, "开始记录", 10, -76, 92, function() StartRaidRecord(false) end)
  CreateButton(miniPanel, "结束保存", 111, -76, 92, StopRaidRecord)
  CreateButton(miniPanel, "查看报告", 212, -76, 92, OpenPanelWithReport)

  local hint = miniPanel:CreateFontString(nil, "OVERLAY", "GameFontDisableSmall")
  hint:SetPoint("TOPLEFT", 10, -106)
  hint:SetWidth(292)
  hint:SetJustifyH("LEFT")
  hint:SetText("进副本自动开始；正常退出游戏后会保存逐Boss报告。")

  RefreshMiniStatus()
end

local function OpenMiniPanel()
  CreateMiniPanel()
  if db then db.miniHidden = false end
  miniPanel:Show()
  RefreshMiniStatus()
end

SLASH_RAIDOPTIMIZERSTRESS1 = "/rostress"
SLASH_RAIDOPTIMIZERSTRESS2 = "/rops"
SLASH_RAIDOPTIMIZERSTRESS3 = "/rotest"
SlashCmdList.RAIDOPTIMIZERSTRESS = function(msg)
  msg = string.lower((msg or ""):match("^%s*(.-)%s*$"))
  if msg == "start" or msg == "record" then
    StartRaidRecord(false, false, { nativeOnly = true, reason = "manual_native" })
  elseif msg == "scene" or msg == "scene start" or msg == "town" or msg == "city" then
    StartRaidRecord(false, false, { nativeOnly = true, sceneMode = true, reason = "manual_scene" })
  elseif msg == "legacy-internal" then
    StartRaidRecord(false, false, { nativeOnly = false, reason = "legacy_internal" })
  elseif msg == "profile" or msg == "waprofile" then
    StartRaidRecord(false, true, { nativeOnly = true, reason = "wa_profile_diagnostic" })
  elseif msg == "profilearm" or msg == "waarm" then
    RaidOptimizerArmWeakAurasBossProfile()
  elseif msg == "stop" or msg == "end" then
    StopRaidRecord()
  elseif msg == "report" then
    if db and (db.raidRecord or db.latestRaidReport) then
      CreatePanel()
      panel:Show()
      local text = db.raidRecord and SummarizeRaidRecord(db.raidRecord, false) or db.latestRaidReport
      AddLine(text)
      Print("实战报告已显示在面板里。")
    else
      Print(LastReportText())
    end
  elseif msg == "full" or msg == "reportfull" then
    if db and (db.raidRecord or db.latestRaidReportFull or db.latestRaidReport) then
      CreatePanel()
      panel:Show()
      ShowRaidRecordFull()
      Print("完整实战报告已显示在面板里。")
    else
      Print(LastReportText())
    end
  elseif msg == "mini" or msg == "button" then
    OpenMiniPanel()
  elseif msg == "status" then
    CreatePanel()
    panel:Show()
    AddLine(RuntimeValidationText())
    Print("流畅度诊断：" .. LoadedText(ADDON) .. "。")
  else
    TogglePanel()
    AddLine(RuntimeValidationText())
  end
end

recorderFrame:RegisterEvent("ENCOUNTER_START")
recorderFrame:RegisterEvent("ENCOUNTER_END")
recorderFrame:RegisterEvent("PLAYER_REGEN_DISABLED")
recorderFrame:RegisterEvent("PLAYER_REGEN_ENABLED")
recorderFrame:RegisterEvent("PLAYER_ENTERING_WORLD")
recorderFrame:RegisterEvent("ZONE_CHANGED_NEW_AREA")
recorderFrame:RegisterEvent("GROUP_ROSTER_UPDATE")
recorderFrame:RegisterEvent("NAME_PLATE_UNIT_ADDED")
recorderFrame:RegisterEvent("NAME_PLATE_UNIT_REMOVED")
recorderFrame:RegisterEvent("PLAYER_LOGOUT")
recorderFrame:SetScript("OnEvent", function(_, event, ...)
  if event == "PLAYER_LOGOUT" then
    if db and db.raidRecord and db.raidRecord.active then
      FinalizeRaidRecord("logout")
    elseif db and db.raidRecord and not db.latestRaidExport then
      FinalizeRaidRecord("logout")
    end
    return
  end
  if event == "PLAYER_ENTERING_WORLD" then
    ScheduleAutoRaidReconcile("player_entering_world", 2)
    return
  elseif event == "ZONE_CHANGED_NEW_AREA" then
    ScheduleAutoRaidReconcile("zone_changed", 1)
    return
  elseif event == "GROUP_ROSTER_UPDATE" then
    ScheduleAutoRaidReconcile("group_update", 2)
    return
  elseif event == "NAME_PLATE_UNIT_ADDED" then
    if recorderActive then nameplateAdds = nameplateAdds + 1 end
    return
  elseif event == "NAME_PLATE_UNIT_REMOVED" then
    if recorderActive then nameplateRemoves = nameplateRemoves + 1 end
    return
  end
  if event == "ENCOUNTER_START" and db and db.waProfileArm and db.waProfileArm.armed then
    if recorderActive then FinalizeRaidRecord("switch_to_wa_profile_boss") end
    StartRaidRecord(false, true, {
      nativeOnly = true,
      reason = "wa_profile_armed_boss",
      autoStopOnEncounterEnd = true,
    })
    db.waProfileArm = nil
  elseif event == "ENCOUNTER_START" and not recorderActive and ShouldAutoRecordEncounter() then
    StartAutomaticRaidRecord("encounter_start")
  elseif event == "PLAYER_REGEN_DISABLED" and not recorderActive and RaidOptimizerIsDiagnosticInstance() then
    StartAutomaticRaidRecord("instance_combat_start")
  end
  if not recorderActive or not db or not db.raidRecord then return end
  if event == "COMBAT_LOG_EVENT_UNFILTERED" then
    HandleRecorderCombatLog()
    return
  end
  if event == "ENCOUNTER_START" then
    local encounterId = select(1, ...)
    local encounterName = select(2, ...)
    local difficultyId = select(3, ...)
    local groupSize = select(4, ...)
    StartBossSegment(db.raidRecord, encounterId, encounterName, difficultyId, groupSize)
    if not db.raidRecord.nativeOnly then
      db.raidRecord.nextPeriodicAttributionAt = Now()
      ArmAttributionWindow(db.raidRecord, "encounter_start", 8, true)
    end
    AddRecordEvent("Boss开始", tostring(encounterName or "未知Boss"))
  elseif event == "ENCOUNTER_END" then
    local encounterName = select(2, ...)
    local success = select(5, ...)
    AddRecordEvent("Boss结束", tostring(encounterName or "未知Boss") .. " / " .. (success == 1 and "击杀" or "结束"))
    FinishBossSegment(db.raidRecord, success)
    local record = db.raidRecord
    if record and record.waProfileCapture and record.autoStopOnEncounterEnd then
      C_Timer.After(0, function()
        local ok, text = FinalizeRaidRecord("wa_profile_encounter_end")
        if ok then
          AddLine(text)
          Print("WA深度归因已随Boss结束自动保存；本轮只用于找根因，不计入FPS成绩。")
          if RefreshMiniStatus then RefreshMiniStatus() end
        end
      end)
    elseif db.waProfileArm and db.waProfileArm.waitForCurrentBoss then
      db.waProfileArm.waitForCurrentBoss = false
      C_Timer.After(0, function()
        if recorderActive then FinalizeRaidRecord("wa_profile_waiting_next_boss") end
        Print("当前Boss低开销记录已保存；下一场Boss将自动进行WA深度归因。")
      end)
    end
  elseif event == "PLAYER_REGEN_DISABLED" then
    AddRecordEvent("进战斗", "进入战斗")
  elseif event == "PLAYER_REGEN_ENABLED" then
    AddRecordEvent("脱战", "离开战斗")
  end
end)

frame:RegisterEvent("ADDON_LOADED")
frame:SetScript("OnEvent", function(_, event, arg1)
  if event == "ADDON_LOADED" and arg1 == ADDON then
    db = RaidOptimizerStressTestDB
    db.history = db.history or {}
    db.raidHistory = db.raidHistory or {}
    CompactRaidHistory()
    local stamp = tostring(RaidOptimizerStressTestInstallStamp or ADDON_VERSION)
    if db.installStamp ~= stamp or db.installedVersion ~= ADDON_VERSION then
      db.installStamp = stamp
      db.installedVersion = ADDON_VERSION
      db.miniHidden = true
    end
    if db.silentStandbyStamp ~= SILENT_STANDBY_STAMP then
      db.silentStandbyStamp = SILENT_STANDBY_STAMP
      db.miniHidden = true
    end
    HookLuaErrorHandler()
    if C_Timer and C_Timer.After then
      C_Timer.After(5, HookLuaErrorHandler)
    end
    if db.raidRecord and db.raidRecord.active then
      pendingContinuationOf = ArchiveInterruptedRecord()
    elseif db.raidRecord and db.latestRaidExport then
      db.raidRecord = nil
    end
    if db.miniHidden == nil then db.miniHidden = true end
    -- Customer builds stay quiet until the user opens the panel from
    -- /rostatus or /rops mini.
    if C_Timer and type(C_Timer.After) == "function" then
      C_Timer.After(3, function() ScheduleAutoRaidReconcile("addon_loaded", 0) end)
    end
    Print("已加载：进入五人或团队副本会自动开始低开销飞行记录；无需再手动点开始。")
  end
end)
