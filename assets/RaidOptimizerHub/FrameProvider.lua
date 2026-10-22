local Hub = RaidOptimizerHub
if not Hub then return end
if type(Hub.GetMode) == "function" and Hub.GetMode() == "native" then return end

local provider = {}
local unitFrames = {}
local frameUnits = setmetatable({}, { __mode = "k" })
local negativeUntil = {}
local scanFrame = CreateFrame("Frame")
local eventFrame = CreateFrame("Frame")
local scanCursor
local scanRunning = false
local activated = false
local generation = 0
local lastScanAt = 0
local callbacks = {}
local SCAN_COOLDOWN_SECONDS = 120
local StartScan

local stats = {
  queries = 0,
  cacheHits = 0,
  directHits = 0,
  nameplateHits = 0,
  misses = 0,
  scans = 0,
  slices = 0,
  framesSeen = 0,
  framesIndexed = 0,
  invalidations = 0,
  scanMs = 0,
  scanCooldownSkips = 0,
  combatScanSkips = 0,
}

local function Copy(source)
  local result = {}
  for key, value in pairs(source or {}) do result[key] = value end
  return result
end

local function FireCallback(event, ...)
  local listeners = callbacks[event]
  if not listeners then return end
  for _, callback in pairs(listeners) do
    xpcall(callback, geterrorhandler(), event, ...)
  end
end

function provider.RegisterCallback(owner, event, callback)
  if type(owner) ~= "string" or type(event) ~= "string" or type(callback) ~= "function" then return false end
  callbacks[event] = callbacks[event] or {}
  callbacks[event][owner] = callback
  activated = true
  if StartScan then StartScan(false) end
  return true
end

function provider.UnregisterCallback(owner, event)
  if callbacks[event] then callbacks[event][owner] = nil end
end

function provider.UnregisterAllCallbacks(owner)
  for _, listeners in pairs(callbacks) do listeners[owner] = nil end
end

local function UnitForFrame(frame)
  if not frame then return nil end
  local unit = frameUnits[frame] or frame.unit or frame.displayedUnit
  if not unit and type(frame.GetAttribute) == "function" then
    local ok, value = pcall(frame.GetAttribute, frame, "unit")
    if ok then unit = value end
  end
  if not unit and type(SecureButton_GetUnit) == "function" then
    local ok, value = pcall(SecureButton_GetUnit, frame)
    if ok then unit = value end
  end
  return type(unit) == "string" and unit or nil
end

local function IndexFrame(frame, explicitUnit)
  local unit = explicitUnit or UnitForFrame(frame)
  if not unit or not UnitExists or not UnitExists(unit) then return false end
  local previousUnit = frameUnits[frame]
  local frames = unitFrames[unit]
  if not frames then
    frames = setmetatable({}, { __mode = "k" })
    unitFrames[unit] = frames
  end
  if not frames[frame] then stats.framesIndexed = stats.framesIndexed + 1 end
  frames[frame] = true
  frameUnits[frame] = unit
  if not previousUnit then
    FireCallback("FRAME_UNIT_ADDED", frame, unit)
  elseif previousUnit ~= unit then
    local previousFrames = unitFrames[previousUnit]
    if previousFrames then previousFrames[frame] = nil end
    FireCallback("FRAME_UNIT_UPDATE", frame, unit, previousUnit)
  end
  return true
end

local function RemoveUnitFrames(unit)
  local frames = unitFrames[unit]
  if not frames then return end
  for frame in pairs(frames) do
    frames[frame] = nil
    if frameUnits[frame] == unit then
      frameUnits[frame] = nil
      FireCallback("FRAME_UNIT_REMOVED", frame, unit)
    end
  end
  unitFrames[unit] = nil
end

local function RemoveInvalidFrames()
  for frame, unit in pairs(frameUnits) do
    if not UnitExists or not UnitExists(unit) then
      local frames = unitFrames[unit]
      if frames then frames[frame] = nil end
      frameUnits[frame] = nil
      FireCallback("FRAME_UNIT_REMOVED", frame, unit)
    end
  end
end

local function DirectFrame(unit)
  if C_NamePlate and type(C_NamePlate.GetNamePlateForUnit) == "function" then
    local plate = C_NamePlate.GetNamePlateForUnit(unit)
    if plate then
      stats.nameplateHits = stats.nameplateHits + 1
      IndexFrame(plate, unit)
      return plate
    end
  end

  if Cell and Cell.unitButtons and Cell.unitButtons[unit] then
    local button = Cell.unitButtons[unit]
    if type(button) == "table" and button[1] then button = button[1] end
    if button then IndexFrame(button, unit); return button end
  end

  local globals = _G
  local candidates
  if unit == "player" then
    candidates = { globals.PlayerFrame, globals.ElvUF_Player }
  elseif unit == "target" then
    candidates = { globals.TargetFrame, globals.ElvUF_Target }
  elseif unit == "focus" then
    candidates = { globals.FocusFrame, globals.ElvUF_Focus }
  elseif unit:match("^raid%d+$") then
    local index = tonumber(unit:match("%d+"))
    candidates = { globals["CompactRaidFrame" .. index], globals["ElvUF_RaidGroup1UnitButton" .. index] }
  elseif unit:match("^party%d+$") then
    local index = tonumber(unit:match("%d+"))
    candidates = { globals["CompactPartyFrameMember" .. index], globals["PartyMemberFrame" .. index] }
  elseif unit:match("^boss%d+$") then
    local index = tonumber(unit:match("%d+"))
    candidates = { globals["Boss" .. index .. "TargetFrame"], globals["ElvUF_Boss" .. index] }
  end
  for _, frame in pairs(candidates or {}) do
    if frame and IndexFrame(frame, unit) then
      stats.directHits = stats.directHits + 1
      return frame
    end
  end
end

local function ResolveUnit(target)
  if type(target) ~= "string" or target == "" then return nil end
  if UnitExists and UnitExists(target) then return target end
  if not UnitExists then return nil end
  local maxRaid = GetNumGroupMembers and GetNumGroupMembers() or 0
  local maxParty = GetNumSubgroupMembers and GetNumSubgroupMembers() or 0
  local units = { "player", "target", "focus", "mouseover" }
  for index = 1, maxRaid do units[#units + 1] = "raid" .. index end
  for index = 1, maxParty do units[#units + 1] = "party" .. index end
  for index = 1, 5 do units[#units + 1] = "boss" .. index end
  for _, unit in ipairs(units) do
    if UnitExists(unit) then
      if UnitGUID and UnitGUID(unit) == target then return unit end
      if UnitName and UnitName(unit) == target then return unit end
    end
  end
end

local function BestIndexed(unit, returnAll)
  local frames = unitFrames[unit]
  if not frames then return nil end
  local all
  for frame in pairs(frames) do
    if UnitForFrame(frame) == unit then
      if returnAll then
        all = all or setmetatable({}, { __mode = "k" })
        all[frame] = frame
      else
        return frame
      end
    else
      frames[frame] = nil
      frameUnits[frame] = nil
    end
  end
  return all
end

local function FinishScan(started)
  scanRunning = false
  scanCursor = nil
  scanFrame:Hide()
  generation = generation + 1
  negativeUntil = {}
  FireCallback("GETFRAME_REFRESH")
  if started > 0 then stats.scanMs = stats.scanMs + (debugprofilestop() - started) end
  if type(Hub.PublishProvider) == "function" then
    Hub.PublishProvider("frames.index", {
      generation = generation,
      framesSeen = stats.framesSeen,
      framesIndexed = stats.framesIndexed,
      scans = stats.scans,
      slices = stats.slices,
    })
  end
end

local function ScanSlice()
  if not scanRunning or type(EnumerateFrames) ~= "function" then FinishScan(0); return end
  local started = debugprofilestop and debugprofilestop() or 0
  local budget = 0.35
  local count = 0
  while count < 240 do
    scanCursor = EnumerateFrames(scanCursor)
    if not scanCursor then FinishScan(started); return end
    stats.framesSeen = stats.framesSeen + 1
    IndexFrame(scanCursor)
    count = count + 1
    if started > 0 and debugprofilestop() - started >= budget then break end
  end
  stats.slices = stats.slices + 1
  if started > 0 then stats.scanMs = stats.scanMs + (debugprofilestop() - started) end
end

StartScan = function(force)
  activated = true
  local now = GetTime and GetTime() or 0
  if scanRunning then return end
  if InCombatLockdown and InCombatLockdown() then
    stats.combatScanSkips = stats.combatScanSkips + 1
    return
  end
  if not force and lastScanAt > 0 and now - lastScanAt < SCAN_COOLDOWN_SECONDS then
    stats.scanCooldownSkips = stats.scanCooldownSkips + 1
    return
  end
  lastScanAt = now
  stats.scans = stats.scans + 1
  scanCursor = nil
  scanRunning = true
  scanFrame:Show()
end

scanFrame:SetScript("OnUpdate", ScanSlice)
scanFrame:Hide()

function provider.GetUnitFrame(selfOrTarget, targetOrOptions, maybeOptions)
  activated = true
  stats.queries = stats.queries + 1
  local target, options
  if selfOrTarget == provider then target, options = targetOrOptions, maybeOptions
  else target, options = selfOrTarget, targetOrOptions end
  local unit = ResolveUnit(target)
  if not unit then stats.misses = stats.misses + 1; return nil end

  local direct = DirectFrame(unit)
  if direct and not (options and options.returnAll) then return direct end
  local cached = BestIndexed(unit, options and options.returnAll)
  if cached then stats.cacheHits = stats.cacheHits + 1; return cached end

  local now = GetTime and GetTime() or 0
  if not negativeUntil[unit] or negativeUntil[unit] <= now then
    negativeUntil[unit] = now + 5
    StartScan(false)
  end
  stats.misses = stats.misses + 1
  return nil
end

provider.GetFrame = provider.GetUnitFrame

function provider.GetUnitNameplate(selfOrUnit, maybeUnit)
  local unit = selfOrUnit == provider and maybeUnit or selfOrUnit
  if C_NamePlate and type(C_NamePlate.GetNamePlateForUnit) == "function" then
    local frame = C_NamePlate.GetNamePlateForUnit(unit)
    if frame then stats.nameplateHits = stats.nameplateHits + 1; IndexFrame(frame, unit) end
    return frame
  end
end

function provider.ScanForUnitFrames() StartScan(false) end
function provider.ForceScanForUnitFrames() StartScan(true) end
function provider.CancelScan() scanRunning = false; scanCursor = nil; scanFrame:Hide() end
function provider.GetStats()
  local result = Copy(stats)
  result.callbackEvents = 0
  result.callbackListeners = 0
  for _, listeners in pairs(callbacks) do
    local eventHasListener = false
    for _ in pairs(listeners) do
      result.callbackListeners = result.callbackListeners + 1
      eventHasListener = true
    end
    if eventHasListener then result.callbackEvents = result.callbackEvents + 1 end
  end
  return result
end
function provider.GetFlightRecorderStats()
  return stats.queries or 0,
    stats.cacheHits or 0,
    stats.directHits or 0,
    stats.nameplateHits or 0,
    stats.misses or 0,
    stats.scans or 0,
    stats.scanMs or 0,
    stats.combatScanSkips or 0
end
function provider.ResetStats() for key in pairs(stats) do stats[key] = 0 end end
function provider.getDefaultOptions() return {} end
function provider.getDefaultFramePriorities() return {} end

Hub.FrameProvider = provider
Hub.GetUnitFrame = function(...) return provider.GetUnitFrame(...) end
Hub.GetUnitNameplate = function(...) return provider.GetUnitNameplate(...) end
Hub.GetFrameProviderStats = function() return provider.GetStats() end
Hub.GetFrameProviderFlightRecorderStats = function() return provider.GetFlightRecorderStats() end

eventFrame:RegisterEvent("PLAYER_ENTERING_WORLD")
eventFrame:RegisterEvent("GROUP_ROSTER_UPDATE")
eventFrame:RegisterEvent("NAME_PLATE_UNIT_ADDED")
eventFrame:RegisterEvent("NAME_PLATE_UNIT_REMOVED")
eventFrame:SetScript("OnEvent", function(_, event, unit)
  -- The provider is an optional shared service.  Do not duplicate the
  -- official LibGetFrame enumeration merely because Hub loaded; activate it
  -- only after a real query, callback subscription or explicit scan request.
  if not activated then return end
  generation = generation + 1
  negativeUntil = {}
  if event == "NAME_PLATE_UNIT_ADDED" and unit then DirectFrame(unit) end
  if event == "NAME_PLATE_UNIT_REMOVED" and unit then RemoveUnitFrames(unit) end
  if event == "PLAYER_ENTERING_WORLD" or event == "GROUP_ROSTER_UPDATE" then
    RemoveInvalidFrames()
    StartScan(false)
  end
end)

SLASH_RAIDOPTIMIZERFRAMES1 = "/roframes"
SlashCmdList.RAIDOPTIMIZERFRAMES = function(message)
  if (message or ""):lower():match("reset") then provider.ResetStats() end
  local s = provider.GetStats()
  return s
end
