local ADDON = ...

local SAMPLE_EVERY = 16
local HISTORY_LIMIT = 20
local state

local function InitializeState()
  RaidOptimizerStressTestDB = RaidOptimizerStressTestDB or {}
  state = RaidOptimizerStressTestDB.nduiRaidAuraProbe or {}
  RaidOptimizerStressTestDB.nduiRaidAuraProbe = state
  state.format = "RO-NDUI-RAID-AURA-PROBE-1"
  state.sampleEvery = SAMPLE_EVERY
  state.calls = tonumber(state.calls) or 0
  state.sampledCalls = tonumber(state.sampledCalls) or 0
  state.sampledMs = tonumber(state.sampledMs) or 0
  state.maxSampleMs = tonumber(state.maxSampleMs) or 0
  state.wrappedFrames = 0
  state.history = state.history or {}
end

local wrapped = setmetatable({}, {__mode = "k"})
local activeEncounter

local function Snapshot()
  return {
    calls = state.calls,
    sampledCalls = state.sampledCalls,
    sampledMs = state.sampledMs,
    maxSampleMs = state.maxSampleMs,
  }
end

local function Delta(after, before)
  local sampledMs = math.max(0, after.sampledMs - before.sampledMs)
  return {
    calls = math.max(0, after.calls - before.calls),
    sampledCalls = math.max(0, after.sampledCalls - before.sampledCalls),
    sampledMs = sampledMs,
    estimatedPostUpdateMs = sampledMs * SAMPLE_EVERY,
    maxSampleMs = activeEncounter and activeEncounter.maxSampleMs or 0,
    scope = "NDui RaidAuras.PostUpdate only; excludes oUF UnitAura scan",
  }
end

local function WrapElement(element)
  if element == nil or wrapped[element] then return false end
  local original = element.PostUpdate
  if type(original) ~= "function" then return false end

  element.PostUpdate = function(...)
    state.calls = state.calls + 1
    local sampled = state.calls % SAMPLE_EVERY == 0
      and type(debugprofilestop) == "function"
    local started = sampled and debugprofilestop() or 0
    original(...)
    if sampled then
      local elapsed = math.max(0, debugprofilestop() - started)
      state.sampledCalls = state.sampledCalls + 1
      state.sampledMs = state.sampledMs + elapsed
      state.maxSampleMs = math.max(state.maxSampleMs, elapsed)
      if activeEncounter then
        activeEncounter.maxSampleMs = math.max(activeEncounter.maxSampleMs or 0, elapsed)
      end
    end
  end
  wrapped[element] = true
  return true
end

local function ScanRaidFrames()
  if type(EnumerateFrames) ~= "function" then
    state.lastScanStatus = "EnumerateFrames unavailable"
    return 0
  end
  local count = 0
  local current
  while true do
    current = EnumerateFrames(current)
    if not current then break end
    if current.mystyle == "raid" and WrapElement(current.RaidAuras) then
      count = count + 1
    end
  end
  state.wrappedFrames = state.wrappedFrames + count
  state.lastScanAdded = count
  state.lastScanStatus = count > 0 and "active" or "waiting_for_raid_frames"
  return count
end

RaidOptimizerNDuiRaidAuraProbeSnapshot = function()
  if not state then return {lastScanStatus = "waiting_for_saved_variables"} end
  local result = Snapshot()
  result.wrappedFrames = state.wrappedFrames
  result.lastScanStatus = state.lastScanStatus
  result.lastEncounter = state.lastEncounter
  return result
end

local frame = CreateFrame("Frame")
frame:RegisterEvent("ADDON_LOADED")
frame:RegisterEvent("PLAYER_ENTERING_WORLD")
frame:RegisterEvent("GROUP_ROSTER_UPDATE")
frame:RegisterEvent("ENCOUNTER_START")
frame:RegisterEvent("ENCOUNTER_END")
frame:SetScript("OnEvent", function(_, event, ...)
  if event == "ADDON_LOADED" then
    local loadedAddon = ...
    if loadedAddon ~= ADDON then return end
    InitializeState()
    frame:UnregisterEvent("ADDON_LOADED")
  elseif event == "PLAYER_ENTERING_WORLD" or event == "GROUP_ROSTER_UPDATE" then
    if not state then return end
    ScanRaidFrames()
  elseif event == "ENCOUNTER_START" then
    if not state then return end
    local encounterID, encounterName = ...
    ScanRaidFrames()
    activeEncounter = {
      encounterID = encounterID,
      encounterName = encounterName,
      maxSampleMs = 0,
      start = Snapshot(),
    }
  elseif event == "ENCOUNTER_END" and activeEncounter then
    local encounterID, encounterName, _, _, success = ...
    if encounterID == activeEncounter.encounterID then
      local result = Delta(Snapshot(), activeEncounter.start)
      result.encounterID = encounterID
      result.encounterName = encounterName or activeEncounter.encounterName
      result.success = success
      state.lastEncounter = result
      state.history[#state.history + 1] = result
      if #state.history > HISTORY_LIMIT then table.remove(state.history, 1) end
      activeEncounter = nil
    end
  end
end)
