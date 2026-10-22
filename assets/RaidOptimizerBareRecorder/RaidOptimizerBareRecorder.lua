local ADDON_NAME = ...
local VERSION = "0.1.0"

-- This addon is intentionally a recorder only. It creates no visible frame,
-- reads no combat log, aura, spell, unit, damage or healing data, and changes
-- no user setting. OnUpdate receives Blizzard's elapsed frame time and only
-- performs fixed numeric accumulation.
local recorder = CreateFrame("Frame")
local session
local currentEncounter
local inCombat = false
local sampleElapsed = 0
local sampleFrames = 0
local sampleWorstMs = 0

local floor = math.floor
local max = math.max
local min = math.min

local function Now()
  if type(GetServerTime) == "function" then
    return GetServerTime()
  end
  return time()
end

local function NewAccumulator()
  return {
    frames = 0,
    elapsedMs = 0,
    worstFrameMs = 0,
    histogramMs = {},
  }
end

local function RecordFrame(target, elapsed, elapsedMs, histogramBucket)
  target.frames = target.frames + 1
  target.elapsedMs = target.elapsedMs + elapsedMs
  if elapsedMs > target.worstFrameMs then
    target.worstFrameMs = elapsedMs
  end
  local histogram = target.histogramMs
  histogram[histogramBucket] = (histogram[histogramBucket] or 0) + 1
end

local function AddMarker(kind, value1, value2)
  if not session then return end
  local markers = session.markers
  markers[#markers + 1] = {
    tMs = floor((GetTime() - session.startedAtGameTime) * 1000 + 0.5),
    kind = kind,
    value1 = value1,
    value2 = value2,
  }
end

local function RecordLocation(reason)
  local name, instanceType, difficultyID, difficultyName, maxPlayers,
    dynamicDifficulty, isDynamic, instanceID = GetInstanceInfo()
  AddMarker("location", reason, {
    name = name or "",
    instanceType = instanceType or "",
    difficultyID = difficultyID or 0,
    difficultyName = difficultyName or "",
    maxPlayers = maxPlayers or 0,
    instanceID = instanceID or 0,
  })
end

local function FinishEncounter(encounterID, encounterName, success)
  if not currentEncounter then return end
  currentEncounter.endedAt = Now()
  currentEncounter.endedAtGameTime = GetTime()
  currentEncounter.success = success
  currentEncounter.endEncounterID = encounterID
  currentEncounter.endEncounterName = encounterName
  AddMarker("encounter_end", encounterID, success)
  currentEncounter = nil
end

local function FlushSample()
  if not session or sampleFrames == 0 then return end
  local samples = session.samples
  if #samples < 21600 then
    samples[#samples + 1] = {
      tMs = floor((GetTime() - session.startedAtGameTime) * 1000 + 0.5),
      frames = sampleFrames,
      elapsedMs = floor(sampleElapsed * 1000 + 0.5),
      worstFrameMs = sampleWorstMs,
      inCombat = inCombat and 1 or 0,
      encounterID = currentEncounter and currentEncounter.encounterID or 0,
    }
  else
    session.sampleOverflow = (session.sampleOverflow or 0) + 1
  end
  sampleElapsed = 0
  sampleFrames = 0
  sampleWorstMs = 0
end

local function StartSession()
  RaidOptimizerBareRecorderDB = RaidOptimizerBareRecorderDB or {}
  local db = RaidOptimizerBareRecorderDB
  db.schema = 1
  db.version = VERSION
  db.sessions = db.sessions or {}
  while #db.sessions >= 12 do
    table.remove(db.sessions, 1)
  end

  local version, build, buildDate, interfaceVersion = GetBuildInfo()
  session = {
    schema = 1,
    recorderVersion = VERSION,
    startedAt = Now(),
    startedAtGameTime = GetTime(),
    gameVersion = version or "",
    gameBuild = build or "",
    gameBuildDate = buildDate or "",
    interfaceVersion = interfaceVersion or 0,
    total = NewAccumulator(),
    combat = NewAccumulator(),
    encounters = {},
    samples = {},
    markers = {},
  }
  db.sessions[#db.sessions + 1] = session
  AddMarker("session_start", ADDON_NAME, VERSION)
  RecordLocation("session_start")
end

recorder:SetScript("OnUpdate", function(_, elapsed)
  if not session or type(elapsed) ~= "number" or elapsed <= 0 then return end

  local elapsedMs = elapsed * 1000
  local histogramBucket = min(500, max(1, floor(elapsedMs + 0.5)))
  RecordFrame(session.total, elapsed, elapsedMs, histogramBucket)
  if inCombat then
    RecordFrame(session.combat, elapsed, elapsedMs, histogramBucket)
  end
  if currentEncounter then
    RecordFrame(currentEncounter.stats, elapsed, elapsedMs, histogramBucket)
  end

  sampleElapsed = sampleElapsed + elapsed
  sampleFrames = sampleFrames + 1
  if elapsedMs > sampleWorstMs then
    sampleWorstMs = elapsedMs
  end
  if sampleElapsed >= 1 then
    FlushSample()
  end
end)

recorder:SetScript("OnEvent", function(_, event, ...)
  if event == "ADDON_LOADED" then
    local loaded = ...
    if loaded == ADDON_NAME then
      StartSession()
    end
  elseif event == "PLAYER_ENTERING_WORLD" then
    RecordLocation("enter_world")
  elseif event == "ZONE_CHANGED_NEW_AREA" then
    RecordLocation("zone_changed")
  elseif event == "PLAYER_REGEN_DISABLED" then
    inCombat = true
    AddMarker("combat_start")
  elseif event == "PLAYER_REGEN_ENABLED" then
    inCombat = false
    AddMarker("combat_end")
  elseif event == "ENCOUNTER_START" then
    local encounterID, encounterName, difficultyID, groupSize = ...
    if currentEncounter then
      FinishEncounter(currentEncounter.encounterID, currentEncounter.name, 0)
    end
    currentEncounter = {
      encounterID = encounterID or 0,
      name = encounterName or "",
      difficultyID = difficultyID or 0,
      groupSize = groupSize or 0,
      startedAt = Now(),
      startedAtGameTime = GetTime(),
      stats = NewAccumulator(),
    }
    local encounters = session.encounters
    encounters[#encounters + 1] = currentEncounter
    AddMarker("encounter_start", encounterID, encounterName)
  elseif event == "ENCOUNTER_END" then
    local encounterID, encounterName, _, _, success = ...
    FinishEncounter(encounterID, encounterName, success)
  elseif event == "PLAYER_LOGOUT" then
    FlushSample()
    if currentEncounter then
      FinishEncounter(currentEncounter.encounterID, currentEncounter.name, 0)
    end
    if session then
      session.endedAt = Now()
      session.endedAtGameTime = GetTime()
      session.completed = true
      AddMarker("session_end")
    end
  end
end)

recorder:RegisterEvent("ADDON_LOADED")
recorder:RegisterEvent("PLAYER_ENTERING_WORLD")
recorder:RegisterEvent("ZONE_CHANGED_NEW_AREA")
recorder:RegisterEvent("PLAYER_REGEN_DISABLED")
recorder:RegisterEvent("PLAYER_REGEN_ENABLED")
recorder:RegisterEvent("ENCOUNTER_START")
recorder:RegisterEvent("ENCOUNTER_END")
recorder:RegisterEvent("PLAYER_LOGOUT")
