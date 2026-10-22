local Hub = RaidOptimizerHub
if not Hub then return end
if type(Hub.GetMode) == "function" and Hub.GetMode() == "native" then return end

local provider = {}
local requests = {}
local unitOwners = {}
local auraFilterOwners = {}
local caches = { unit = {}, health = {}, aura = {}, cast = {} }
local dirty = { unit = {}, health = {}, aura = {}, cast = {} }
local pendingAuraUpdates = {}
local pendingAuraDirect = {}
local pendingAuraSignatures = {}
local pendingAuraSignals = {}
local pendingAuraSignalUpdates = {}
local canonicalAuraBlocked = {}
local EMPTY_TABLE = {}
local FULL_AURA_SIGNAL = { isFullUpdate = true }
local generation = 0
local auraStructureGeneration = 0
local flushPending = false
local MAX_REFRESHES_PER_FRAME = 80
local eventFrame = CreateFrame("Frame")
local EnsureFlushDriver
local DisableFlushDriver
local ReconcileEventRegistrations
local vfAuraInvalidationRequested = false
local registeredEvents = {}

local stats = {
  queries = 0,
  cacheHits = 0,
  cacheMisses = 0,
  refreshes = 0,
  unitRefreshes = 0,
  healthRefreshes = 0,
  auraRefreshes = 0,
  fullAuraUpdates = 0,
  incrementalAuraUpdates = 0,
  incrementalAuraAdds = 0,
  incrementalAuraChanges = 0,
  incrementalAuraRemoves = 0,
  incrementalAuraFallbacks = 0,
  incrementalListPatches = 0,
  incrementalListPatchAuras = 0,
  incrementalListRebuilds = 0,
  legacyAuraDeltaPublishes = 0,
  legacyAuraDeltaAuras = 0,
  modernAuraScans = 0,
  legacyAuraScans = 0,
  legacyTupleScans = 0,
  legacyTupleAuras = 0,
  legacyAuraParityFailures = 0,
  castRefreshes = 0,
  events = 0,
  dirtyMarks = 0,
  coalesced = 0,
  publishes = 0,
  refreshMs = 0,
  refreshSamples = 0,
  trackedUnits = 0,
  owners = 0,
  releasedUnits = 0,
  releasedOwners = 0,
  nameplateReleases = 0,
  rosterReleases = 0,
  flushes = 0,
  budgetYields = 0,
  deferredRefreshes = 0,
  maxFlushWork = 0,
  avoidedAuraScans = 0,
  ignoredIncrementalAuras = 0,
  auraFilterExtensions = 0,
  auraInstanceQueries = 0,
  auraInstanceHits = 0,
  auraInstanceFallbacks = 0,
  auraSpellQueries = 0,
  auraSpellHits = 0,
  auraSpellMisses = 0,
  acquireCalls = 0,
  acquireFastHits = 0,
  auraAcquireFastHits = 0,
  auraSnapshotFastHits = 0,
  legacyAuraListQueries = 0,
  legacyAuraListHits = 0,
  legacyAuraListFallbacks = 0,
  directAuraUpdates = 0,
  mergedAuraUpdates = 0,
  directAuraMutationFallbacks = 0,
  auraSignalsQueued = 0,
  auraSignalsCoalesced = 0,
  auraSignalsPublished = 0,
  auraSignalIncrementalHints = 0,
  auraSignalFullFallbacks = 0,
  canonicalAuraScans = 0,
  canonicalAuraAuras = 0,
  canonicalAuraFallbacks = 0,
  canonicalAuraIdentityFailures = 0,
  eventRegistrations = 0,
  eventUnregistrations = 0,
  inactiveEventSkips = 0,
  flushDriverStarts = 0,
  flushDriverStops = 0,
}
local REFRESH_PROFILE_INTERVAL = 32

local UNIT_EVENTS = {
  UNIT_HEALTH = "health", UNIT_MAXHEALTH = "health",
  UNIT_POWER_UPDATE = "unit", UNIT_MAXPOWER = "unit",
  UNIT_DISPLAYPOWER = "unit", UNIT_NAME_UPDATE = "unit",
  UNIT_CONNECTION = "unit", UNIT_FLAGS = "unit",
  UNIT_AURA = "aura",
  UNIT_SPELLCAST_START = "cast", UNIT_SPELLCAST_STOP = "cast",
  UNIT_SPELLCAST_FAILED = "cast", UNIT_SPELLCAST_INTERRUPTED = "cast",
  UNIT_SPELLCAST_DELAYED = "cast", UNIT_SPELLCAST_CHANNEL_START = "cast",
  UNIT_SPELLCAST_CHANNEL_UPDATE = "cast", UNIT_SPELLCAST_CHANNEL_STOP = "cast",
  UNIT_SPELLCAST_INTERRUPTIBLE = "cast", UNIT_SPELLCAST_NOT_INTERRUPTIBLE = "cast",
}

local function Copy(source)
  local result = {}
  for key, value in pairs(source or {}) do result[key] = value end
  return result
end

local function IsTracked(unit)
  return type(unit) == "string" and unitOwners[unit] and next(unitOwners[unit]) ~= nil
end

local function IsKindTracked(unit, kind)
  local owners = unitOwners[unit]
  if not owners then return false end
  for owner in pairs(owners) do
    local ownerRequests = requests[owner]
    if ownerRequests and ownerRequests[unit] and ownerRequests[unit][kind] then return true end
  end
  return false
end

local function CountTracked()
  local count = 0
  for unit in pairs(unitOwners) do if IsTracked(unit) then count = count + 1 end end
  stats.trackedUnits = count
end

local function MarkDirty(unit, kind)
  if not IsTracked(unit) or not IsKindTracked(unit, kind) then return end
  stats.dirtyMarks = stats.dirtyMarks + 1
  if dirty[kind][unit] then stats.coalesced = stats.coalesced + 1 end
  dirty[kind][unit] = true
  flushPending = true
  if EnsureFlushDriver then EnsureFlushDriver() end
end

local function SafeCall(fn, ...)
  if type(fn) ~= "function" then return nil end
  local ok, a, b, c, d, e, f, g, h, i, j, k, l, m, n, o, p = pcall(fn, ...)
  if not ok then return nil end
  return a, b, c, d, e, f, g, h, i, j, k, l, m, n, o, p
end

local function RefreshUnit(unit)
  generation = generation + 1
  local exists = type(UnitExists) ~= "function" or UnitExists(unit)
  local value = {
    unit = unit, generation = generation, exists = exists and true or false,
    guid = exists and SafeCall(UnitGUID, unit) or nil,
    name = exists and SafeCall(UnitName, unit) or nil,
    health = exists and (SafeCall(UnitHealth, unit) or 0) or 0,
    healthMax = exists and (SafeCall(UnitHealthMax, unit) or 0) or 0,
    power = exists and (SafeCall(UnitPower, unit) or 0) or 0,
    powerMax = exists and (SafeCall(UnitPowerMax, unit) or 0) or 0,
    powerType = exists and SafeCall(UnitPowerType, unit) or nil,
    connected = exists and SafeCall(UnitIsConnected, unit) ~= false or false,
    dead = exists and SafeCall(UnitIsDeadOrGhost, unit) and true or false,
    visible = exists and SafeCall(UnitIsVisible, unit) ~= false or false,
    updatedAt = type(GetTime) == "function" and GetTime() or 0,
  }
  caches.unit[unit] = value
  stats.refreshes, stats.unitRefreshes = stats.refreshes + 1, stats.unitRefreshes + 1
  return value
end

local function ReadAura(unit, index, filter)
  local name, icon, count, dispelType, duration, expirationTime, source, stealable,
    nameplateShowPersonal, spellId, canApplyAura, bossAura, castByPlayer,
    nameplateShowAll, timeMod, point1 = SafeCall(UnitAura, unit, index, filter)
  if not name then return nil end
  return {
    name = name, icon = icon, count = count or 0, dispelType = dispelType,
    duration = duration or 0, expirationTime = expirationTime or 0,
    source = source, stealable = stealable and true or false,
    spellId = spellId, canApplyAura = canApplyAura and true or false,
    bossAura = bossAura and true or false,
    castByPlayer = castByPlayer and true or false,
    nameplateShowPersonal = nameplateShowPersonal and true or false,
    nameplateShowAll = nameplateShowAll and true or false, timeMod = timeMod or 1,
    point1 = point1,
    filter = type(filter) == "string" and filter:find("HARMFUL", 1, true)
      and "HARMFUL" or "HELPFUL",
  }
end

-- Keep the exact legacy UnitAura tuple for non-Retail WeakAuras. Named fields
-- preserve nil/false/zero distinctions while the private instance ID is used
-- only to prove that the legacy index and the modern provider index agree.
local function TryReadNativeLegacyAuraTuple(unit, index, filter)
  if type(UnitAura) ~= "function" then return false end
  local ok, name, icon, stacks, debuffClass, duration, expirationTime, unitCaster,
    isStealable, nameplateShowPersonal, spellId, canApplyAura, isBossDebuff,
    isCastByPlayer, nameplateShowAll, modRate, point1 =
    pcall(UnitAura, unit, index, filter)
  if not ok then return false end
  if not name then return true end
  return true, {
    name = name, icon = icon, stacks = stacks, debuffClass = debuffClass,
    duration = duration, expirationTime = expirationTime,
    unitCaster = unitCaster, isStealable = isStealable,
    nameplateShowPersonal = nameplateShowPersonal, spellId = spellId,
    canApplyAura = canApplyAura, isBossDebuff = isBossDebuff,
    isCastByPlayer = isCastByPlayer, nameplateShowAll = nameplateShowAll,
    modRate = modRate, point1 = point1,
  }
end

local function ReadLegacyAuraTuple(unit, index, filter)
  local ok, tuple = TryReadNativeLegacyAuraTuple(unit, index, filter)
  if not ok or not tuple then return nil end
  local modern
  if C_UnitAuras and type(C_UnitAuras.GetAuraDataByIndex) == "function" then
    modern = SafeCall(C_UnitAuras.GetAuraDataByIndex, unit, index, filter)
  end
  tuple.auraInstanceID = type(modern) == "table" and modern.auraInstanceID or nil
  return tuple
end

local function RebuildLegacyAuraList(value, filter)
  if type(value) ~= "table" or type(value.unit) ~= "string"
    or (filter ~= "HELPFUL" and filter ~= "HARMFUL")
  then return false end
  local list = {}
  local modernList = filter == "HARMFUL" and value.modernHarmful or value.modernHelpful
  local identitySafe = type(modernList) == "table"
  local seenSpellIds, seenInstances = {}, {}
  stats.legacyTupleScans = stats.legacyTupleScans + 1
  for index = 1, 255 do
    local tuple = ReadLegacyAuraTuple(value.unit, index, filter)
    if not tuple then break end
    list[index] = tuple
    stats.legacyTupleAuras = stats.legacyTupleAuras + 1
    local modern = modernList and modernList[index]
    local duplicateIdentity = type(tuple.spellId) == "number"
      and seenSpellIds[tuple.spellId]
    if type(modern) ~= "table" or type(tuple.name) ~= "string"
      or type(tuple.spellId) ~= "number" or duplicateIdentity
      or seenInstances[tuple.auraInstanceID]
      or tuple.auraInstanceID ~= modern.auraInstanceID
      or tuple.name ~= modern.name or tuple.spellId ~= modern.spellId
    then
      identitySafe = false
    end
    if type(tuple.spellId) == "number" then seenSpellIds[tuple.spellId] = true end
    if tuple.auraInstanceID ~= nil then seenInstances[tuple.auraInstanceID] = true end
  end
  if modernList and #list ~= #modernList then identitySafe = false end
  value.legacyHelpful = filter == "HELPFUL" and list or value.legacyHelpful
  value.legacyHarmful = filter == "HARMFUL" and list or value.legacyHarmful
  value.legacyIdentitySafe = value.legacyIdentitySafe or {}
  value.legacyIdentitySafe[filter] = identitySafe and true or false
  return identitySafe
end

local function NormalizeAuraFilter(filter)
  if type(filter) ~= "string" then return nil end
  return filter:find("HARMFUL", 1, true) and "HARMFUL" or "HELPFUL"
end

-- Modifier filters such as RAID and PLAYER have stricter game semantics than
-- the shared broad cache. Let callers fall back to the native API for those.
local function ShareableAuraFilter(filter)
  if type(filter) ~= "string" then return nil end
  if filter == "HELPFUL" or filter == "HARMFUL" then return filter end
  local normalized = filter:upper():match("^%s*(.-)%s*$")
  if normalized == "HELPFUL" or normalized == "HARMFUL" then return normalized end
  return nil
end

local function OwnerHasSpecificAuraDemand(owner, unit)
  local filters = auraFilterOwners[unit]
  return filters and ((filters.HELPFUL and filters.HELPFUL[owner])
    or (filters.HARMFUL and filters.HARMFUL[owner]))
end

local function HasGenericAuraDemand(unit)
  local owners = unitOwners[unit]
  if not owners then return false end
  for owner in pairs(owners) do
    local ownerRequests = requests[owner]
    if ownerRequests and ownerRequests[unit] and ownerRequests[unit].aura
      and not OwnerHasSpecificAuraDemand(owner, unit)
    then
      return true
    end
  end
  return false
end

local function WantsAuraFilter(unit, filter)
  if HasGenericAuraDemand(unit) then return true end
  local filters = auraFilterOwners[unit]
  local owners = filters and filters[filter]
  return owners and next(owners) ~= nil or false
end

local function NormalizeModernAura(data)
  if type(data) ~= "table" then return nil end
  return {
    name = data.name, icon = data.icon, count = data.applications or 0,
    dispelType = data.dispelName, duration = data.duration or 0,
    expirationTime = data.expirationTime or 0, source = data.sourceUnit,
    stealable = data.isStealable and true or false, spellId = data.spellId,
    canApplyAura = data.canApplyAura and true or false,
    bossAura = data.isBossAura and true or false,
    castByPlayer = data.isFromPlayerOrPlayerPet and true or false,
    nameplateShowPersonal = data.nameplateShowPersonal and true or false,
    nameplateShowAll = data.nameplateShowAll and true or false,
    timeMod = data.timeMod or 1, auraInstanceID = data.auraInstanceID,
    point1 = data.points and data.points[1] or nil,
  }
end

local function TryReadModernAuraByIndex(unit, index, filter)
  local getter = C_UnitAuras and C_UnitAuras.GetAuraDataByIndex
  if type(getter) ~= "function" then return false end
  local ok, data = pcall(getter, unit, index, filter)
  if not ok then return false end
  return true, data
end

local function BlockCanonicalAuraFilter(unit, filter)
  canonicalAuraBlocked[unit] = canonicalAuraBlocked[unit] or {}
  if not canonicalAuraBlocked[unit][filter] then
    canonicalAuraBlocked[unit][filter] = true
    stats.canonicalAuraIdentityFailures = stats.canonicalAuraIdentityFailures + 1
  end
end

local function CanonicalAuraFilterAllowed(unit, filter)
  return not (canonicalAuraBlocked[unit] and canonicalAuraBlocked[unit][filter])
end

-- Titan exposes the modern indexed aura record while non-Retail WA still
-- consumes the legacy 16-value UnitAura tuple. Read both at the same index and
-- build every provider view in one pass. The exact legacy tuple remains the
-- authority for UnitAura-compatible accessors, while the normalized snapshot
-- intentionally keeps the historical NormalizeModernAura contract consumed
-- by the live v18 WA bridge. Any identity disagreement aborts this path before
-- the result is committed.
local function BuildCanonicalAuraFilter(unit, filter)
  local result = { legacy = {}, entries = {} }
  local seenInstances, seenSpellIds = {}, {}
  stats.canonicalAuraScans = stats.canonicalAuraScans + 1
  stats.modernAuraScans = stats.modernAuraScans + 1
  for index = 1, 256 do
    local nativeOk, tuple = TryReadNativeLegacyAuraTuple(unit, index, filter)
    local modernOk, data = TryReadModernAuraByIndex(unit, index, filter)
    if not nativeOk or not modernOk then
      stats.canonicalAuraFallbacks = stats.canonicalAuraFallbacks + 1
      BlockCanonicalAuraFilter(unit, filter)
      return false
    end
    if not tuple or data == nil then
      if not tuple then stats.legacyTupleScans = stats.legacyTupleScans + 1 end
      if tuple ~= nil or data ~= nil then
        stats.canonicalAuraFallbacks = stats.canonicalAuraFallbacks + 1
        BlockCanonicalAuraFilter(unit, filter)
        return false
      end
      return true, result
    end
    if index > 255 or type(data) ~= "table"
      or type(tuple.name) ~= "string" or type(tuple.spellId) ~= "number"
      or type(data.auraInstanceID) ~= "number"
      or seenInstances[data.auraInstanceID] or seenSpellIds[tuple.spellId]
      or data.name ~= tuple.name or data.spellId ~= tuple.spellId
    then
      stats.canonicalAuraFallbacks = stats.canonicalAuraFallbacks + 1
      BlockCanonicalAuraFilter(unit, filter)
      return false
    end
    seenInstances[data.auraInstanceID] = true
    seenSpellIds[tuple.spellId] = true
    tuple.auraInstanceID = data.auraInstanceID
    local normalized = NormalizeModernAura(data)
    if not normalized then
      stats.canonicalAuraFallbacks = stats.canonicalAuraFallbacks + 1
      BlockCanonicalAuraFilter(unit, filter)
      return false
    end
    normalized.filter = filter
    result.legacy[index] = tuple
    result.entries[index] = { data = data, normalized = normalized, filter = filter }
    stats.canonicalAuraAuras = stats.canonicalAuraAuras + 1
    stats.legacyTupleAuras = stats.legacyTupleAuras + 1
  end
  stats.canonicalAuraFallbacks = stats.canonicalAuraFallbacks + 1
  BlockCanonicalAuraFilter(unit, filter)
  return false
end

local function CommitCanonicalAuraFilter(value, filter, result)
  if type(result) ~= "table" then return false end
  for _, entry in ipairs(result.entries) do
    local auraInstanceID = entry.data and entry.data.auraInstanceID
    if not auraInstanceID or value.modernByInstance[auraInstanceID] then return false end
  end
  local normalizedList, modernList
  if filter == "HELPFUL" then
    value.helpful = value.helpful or {}
    value.modernHelpful = value.modernHelpful or {}
    value.legacyHelpful = result.legacy
    normalizedList, modernList = value.helpful, value.modernHelpful
  else
    value.harmful = value.harmful or {}
    value.modernHarmful = value.modernHarmful or {}
    value.legacyHarmful = result.legacy
    normalizedList, modernList = value.harmful, value.modernHarmful
  end
  for index, entry in ipairs(result.entries) do
    value.modernOrder = value.modernOrder + 1
    entry.order, entry.listIndex = value.modernOrder, index
    normalizedList[index], modernList[index] = entry.normalized, entry.data
    value.modernByInstance[entry.data.auraInstanceID] = entry
    local spellId = entry.normalized.spellId
    if spellId then
      local spellList = value.bySpellId[spellId]
      if not spellList then spellList = {}; value.bySpellId[spellId] = spellList end
      spellList[#spellList + 1] = entry.normalized
      entry.spellIndex = #spellList
    end
  end
  value.scannedFilters[filter] = true
  value.legacyIdentitySafe[filter] = true
  return true
end

local function RebuildModernAuraLists(value)
  local entries = {}
  for _, entry in pairs(value.modernByInstance or {}) do entries[#entries + 1] = entry end
  table.sort(entries, function(a, b) return (a.order or 0) < (b.order or 0) end)
  value.helpful, value.harmful = {}, {}
  value.modernHelpful, value.modernHarmful = {}, {}
  value.bySpellId = {}
  for _, entry in ipairs(entries) do
    local normalized = NormalizeModernAura(entry.data)
    entry.normalized, entry.spellIndex = normalized, nil
    if normalized then normalized.filter = entry.filter end
    local normalizedList = entry.filter == "HELPFUL" and value.helpful or value.harmful
    local modernList = entry.filter == "HELPFUL" and value.modernHelpful or value.modernHarmful
    if normalized and normalizedList and modernList then
      normalizedList[#normalizedList + 1] = normalized
      modernList[#modernList + 1] = entry.data
      entry.listIndex = #normalizedList
      local spellId = normalized.spellId
      if spellId then
        local spellList = value.bySpellId[spellId]
        if not spellList then spellList = {}; value.bySpellId[spellId] = spellList end
        spellList[#spellList + 1] = normalized
        entry.spellIndex = #spellList
      end
    end
  end

  for _, filter in ipairs({ "HELPFUL", "HARMFUL" }) do
    if value.scannedFilters and value.scannedFilters[filter] then
      -- This independently proves whether modern index/instance identity may
      -- be reused by exact legacy accessors and incremental deltas. It must not
      -- alter the historical modern-normalized snapshot seen by the v18 bridge.
      RebuildLegacyAuraList(value, filter)
    end
  end
  value.listsDirty = false
  auraStructureGeneration = auraStructureGeneration + 1
  value.structureEpoch = auraStructureGeneration
end

local function PatchUpdatedAuraLists(value, changedEntries)
  if value.listsDirty or not changedEntries or #changedEntries == 0 then return false end
  local rawByEntry = {}
  for _, entry in ipairs(changedEntries) do
    local normalizedList = entry.filter == "HELPFUL" and value.helpful or value.harmful
    local modernList = entry.filter == "HELPFUL" and value.modernHelpful or value.modernHarmful
    local legacyList = entry.filter == "HELPFUL" and value.legacyHelpful or value.legacyHarmful
    local spellList = value.bySpellId and value.bySpellId[entry.data.spellId]
    if type(normalizedList) ~= "table" or type(modernList) ~= "table"
      or type(spellList) ~= "table" or not entry.normalized
      or not (value.legacyIdentitySafe and value.legacyIdentitySafe[entry.filter])
      or type(legacyList) ~= "table"
      or normalizedList[entry.listIndex] ~= entry.normalized
      or modernList[entry.listIndex] == nil
      or spellList[entry.spellIndex] ~= entry.normalized
    then
      return false
    end
    local raw = ReadLegacyAuraTuple(value.unit, entry.listIndex, entry.filter)
    if type(raw) ~= "table" or raw.auraInstanceID ~= entry.data.auraInstanceID
      or raw.name ~= entry.data.name or raw.spellId ~= entry.data.spellId
    then
      return false
    end
    rawByEntry[entry] = raw
  end

  local helpful, harmful, modernHelpful, modernHarmful, legacyHelpful, legacyHarmful
  local bySpellId = Copy(value.bySpellId)
  local copiedSpellLists = {}
  for _, entry in ipairs(changedEntries) do
    -- Preserve the live v18 bridge's historical modern-normalized contract;
    -- the exact UnitAura tuple above is used only to prove index identity.
    local normalized = NormalizeModernAura(entry.data)
    if not normalized then return false end
    normalized.filter = entry.filter
    entry.normalized = normalized
    if entry.filter == "HELPFUL" then
      helpful = helpful or Copy(value.helpful)
      modernHelpful = modernHelpful or Copy(value.modernHelpful)
      legacyHelpful = legacyHelpful or Copy(value.legacyHelpful)
      helpful[entry.listIndex] = normalized
      modernHelpful[entry.listIndex] = entry.data
      legacyHelpful[entry.listIndex] = rawByEntry[entry]
    else
      harmful = harmful or Copy(value.harmful)
      modernHarmful = modernHarmful or Copy(value.modernHarmful)
      legacyHarmful = legacyHarmful or Copy(value.legacyHarmful)
      harmful[entry.listIndex] = normalized
      modernHarmful[entry.listIndex] = entry.data
      legacyHarmful[entry.listIndex] = rawByEntry[entry]
    end
    local spellId = normalized.spellId
    local spellList = copiedSpellLists[spellId]
    if not spellList then
      spellList = Copy(value.bySpellId[spellId])
      copiedSpellLists[spellId] = spellList
      bySpellId[spellId] = spellList
    end
    spellList[entry.spellIndex] = normalized
  end
  value.helpful = helpful or value.helpful
  value.harmful = harmful or value.harmful
  value.modernHelpful = modernHelpful or value.modernHelpful
  value.modernHarmful = modernHarmful or value.modernHarmful
  value.legacyHelpful = legacyHelpful or value.legacyHelpful
  value.legacyHarmful = legacyHarmful or value.legacyHarmful
  value.bySpellId = bySpellId
  return true
end

local function EnsureAuraLists(value)
  if value and value.listsDirty and type(value.modernByInstance) == "table" then
    RebuildModernAuraLists(value)
  end
  return value
end

local function RefreshAura(unit)
  generation = generation + 1
  local value = {
    unit = unit, generation = generation, helpful = {}, harmful = {}, bySpellId = {},
    modernHelpful = nil, modernHarmful = nil, modernByInstance = nil, modernOrder = 0,
    legacyHelpful = nil, legacyHarmful = nil, legacyIdentitySafe = {},
    scannedFilters = {}, unitGUID = SafeCall(UnitGUID, unit),
    unitName = SafeCall(UnitName, unit),
  }
  local exists = type(UnitExists) ~= "function" or UnitExists(unit)
  local modernAvailable = C_UnitAuras and type(C_UnitAuras.GetUnitAuras) == "function"
  if exists and modernAvailable then
    local wantsHelpful = WantsAuraFilter(unit, "HELPFUL")
    local wantsHarmful = WantsAuraFilter(unit, "HARMFUL")
    local canonicalAvailable = type(C_UnitAuras.GetAuraDataByIndex) == "function"
      and (not wantsHelpful or CanonicalAuraFilterAllowed(unit, "HELPFUL"))
      and (not wantsHarmful or CanonicalAuraFilterAllowed(unit, "HARMFUL"))
    local helpfulResult, harmfulResult
    local canonicalSafe = canonicalAvailable
    if canonicalSafe and wantsHelpful then
      canonicalSafe, helpfulResult = BuildCanonicalAuraFilter(unit, "HELPFUL")
    end
    if canonicalSafe and wantsHarmful then
      canonicalSafe, harmfulResult = BuildCanonicalAuraFilter(unit, "HARMFUL")
    end
    value.modernHelpful = wantsHelpful and {} or nil
    value.modernHarmful = wantsHarmful and {} or nil
    value.modernByInstance = {}
    stats.avoidedAuraScans = stats.avoidedAuraScans + (wantsHelpful and 0 or 1) + (wantsHarmful and 0 or 1)
    if canonicalSafe then
      canonicalSafe = (not wantsHelpful
        or CommitCanonicalAuraFilter(value, "HELPFUL", helpfulResult))
        and (not wantsHarmful
          or CommitCanonicalAuraFilter(value, "HARMFUL", harmfulResult))
    end
    if canonicalSafe then
      value.listsDirty = false
      auraStructureGeneration = auraStructureGeneration + 1
      value.structureEpoch = auraStructureGeneration
    else
      -- Transactional rollback: the canonical temporary result is never
      -- exposed. Re-run the previous bulk-modern + exact-legacy path.
      value.helpful, value.harmful, value.bySpellId = {}, {}, {}
      value.modernHelpful = wantsHelpful and {} or nil
      value.modernHarmful = wantsHarmful and {} or nil
      value.legacyHelpful, value.legacyHarmful, value.legacyIdentitySafe = nil, nil, {}
      value.modernByInstance, value.modernOrder, value.scannedFilters = {}, 0, {}
      local helpful = wantsHelpful and (SafeCall(C_UnitAuras.GetUnitAuras, unit, "HELPFUL") or {}) or nil
      local harmful = wantsHarmful and (SafeCall(C_UnitAuras.GetUnitAuras, unit, "HARMFUL") or {}) or nil
      value.scannedFilters.HELPFUL = wantsHelpful and true or nil
      value.scannedFilters.HARMFUL = wantsHarmful and true or nil
      stats.modernAuraScans = stats.modernAuraScans
        + (wantsHelpful and 1 or 0) + (wantsHarmful and 1 or 0)
      local pairsToScan = {}
      if helpful then pairsToScan[#pairsToScan + 1] = { helpful, "HELPFUL" } end
      if harmful then pairsToScan[#pairsToScan + 1] = { harmful, "HARMFUL" } end
      for _, pair in ipairs(pairsToScan) do
        for _, data in ipairs(pair[1]) do
          if data.auraInstanceID then
            value.modernOrder = value.modernOrder + 1
            value.modernByInstance[data.auraInstanceID] = {
              data = data, filter = pair[2], order = value.modernOrder,
            }
          end
        end
      end
      RebuildModernAuraLists(value)
    end
  elseif type(UnitAura) == "function" and exists then
    for _, filter in ipairs({ "HELPFUL", "HARMFUL" }) do
      if WantsAuraFilter(unit, filter) then
        value.scannedFilters[filter] = true
        stats.legacyAuraScans = stats.legacyAuraScans + 1
        local target = filter == "HELPFUL" and value.helpful or value.harmful
        for index = 1, 80 do
          local aura = ReadAura(unit, index, filter)
          if not aura then break end
          target[#target + 1] = aura
          if aura.spellId then
            local list = value.bySpellId[aura.spellId]
            if not list then list = {}; value.bySpellId[aura.spellId] = list end
            list[#list + 1] = aura
          end
        end
      else
        stats.avoidedAuraScans = stats.avoidedAuraScans + 1
      end
    end
  end
  if not value.structureEpoch then
    auraStructureGeneration = auraStructureGeneration + 1
    value.structureEpoch = auraStructureGeneration
  end
  value.updatedAt = type(GetTime) == "function" and GetTime() or 0
  caches.aura[unit] = value
  stats.refreshes, stats.auraRefreshes = stats.refreshes + 1, stats.auraRefreshes + 1
  stats.fullAuraUpdates = stats.fullAuraUpdates + 1
  return value
end

local function ExtendAuraFilter(unit, filter)
  local value = caches.aura[unit]
  if not value or (value.scannedFilters and value.scannedFilters[filter]) then return value end
  local exists = type(UnitExists) ~= "function" or UnitExists(unit)
  if not exists then return false end
  if C_UnitAuras and type(C_UnitAuras.GetUnitAuras) == "function"
    and type(value.modernByInstance) == "table"
  then
    local canonicalSafe, result = false, nil
    if CanonicalAuraFilterAllowed(unit, filter)
      and type(C_UnitAuras.GetAuraDataByIndex) == "function"
    then
      canonicalSafe, result = BuildCanonicalAuraFilter(unit, filter)
    end
    canonicalSafe = canonicalSafe and CommitCanonicalAuraFilter(value, filter, result)
    if canonicalSafe then
      value.listsDirty = false
      auraStructureGeneration = auraStructureGeneration + 1
      value.structureEpoch = auraStructureGeneration
    else
      -- Restore the original extension path. RebuildModernAuraLists replaces
      -- every derived list, so a failed temporary canonical result cannot leak.
      local auras = SafeCall(C_UnitAuras.GetUnitAuras, unit, filter) or {}
      stats.modernAuraScans = stats.modernAuraScans + 1
      for _, data in ipairs(auras) do
        if data.auraInstanceID then
          value.modernOrder = (value.modernOrder or 0) + 1
          value.modernByInstance[data.auraInstanceID] = {
            data = data, filter = filter, order = value.modernOrder,
          }
        end
      end
      value.scannedFilters[filter] = true
      RebuildModernAuraLists(value)
    end
  elseif type(UnitAura) == "function" then
    local target = filter == "HELPFUL" and value.helpful or value.harmful
    stats.legacyAuraScans = stats.legacyAuraScans + 1
    for index = 1, 80 do
      local aura = ReadAura(unit, index, filter)
      if not aura then break end
      target[#target + 1] = aura
      if aura.spellId then
        local list = value.bySpellId[aura.spellId]
        if not list then list = {}; value.bySpellId[aura.spellId] = list end
        list[#list + 1] = aura
      end
    end
    value.scannedFilters[filter] = true
  else
    return false
  end
  generation = generation + 1
  value.generation = generation
  value.updatedAt = type(GetTime) == "function" and GetTime() or 0
  stats.refreshes = stats.refreshes + 1
  stats.auraRefreshes = stats.auraRefreshes + 1
  stats.auraFilterExtensions = stats.auraFilterExtensions + 1
  return value
end

local function AuraFilter(data, existing)
  if data.isHelpful then return "HELPFUL" end
  if data.isHarmful then return "HARMFUL" end
  return existing and existing.filter or nil
end

local function MixAuraUpdateSignature(signature, value)
  value = tonumber(value)
  if not value then return nil end
  return (signature * 65599 + (value % 2147483647)) % 2147483647
end

local function ValidateDirectAuraUpdate(updateInfo)
  if type(updateInfo) ~= "table" or updateInfo.isFullUpdate then return nil end
  local added = updateInfo.addedAuras or EMPTY_TABLE
  local updated = updateInfo.updatedAuraInstanceIDs or EMPTY_TABLE
  local removed = updateInfo.removedAuraInstanceIDs or EMPTY_TABLE
  if type(added) ~= "table" or type(updated) ~= "table" or type(removed) ~= "table" then return nil end

  local signature = 5381
  signature = MixAuraUpdateSignature(signature, #added + 1)
  for index, data in ipairs(added) do
    local auraInstanceID = data and data.auraInstanceID
    if type(auraInstanceID) ~= "number" then return nil end
    for prior = 1, index - 1 do
      if added[prior] and added[prior].auraInstanceID == auraInstanceID then return nil end
    end
    for _, value in ipairs(updated) do if value == auraInstanceID then return nil end end
    for _, value in ipairs(removed) do if value == auraInstanceID then return nil end end
    signature = MixAuraUpdateSignature(signature, auraInstanceID)
  end
  signature = MixAuraUpdateSignature(signature, #updated + 3)
  for index, auraInstanceID in ipairs(updated) do
    if type(auraInstanceID) ~= "number" then return nil end
    for prior = 1, index - 1 do if updated[prior] == auraInstanceID then return nil end end
    for _, value in ipairs(removed) do if value == auraInstanceID then return nil end end
    signature = MixAuraUpdateSignature(signature, auraInstanceID)
  end
  signature = MixAuraUpdateSignature(signature, #removed + 7)
  for index, auraInstanceID in ipairs(removed) do
    if type(auraInstanceID) ~= "number" then return nil end
    for prior = 1, index - 1 do if removed[prior] == auraInstanceID then return nil end end
    signature = MixAuraUpdateSignature(signature, auraInstanceID)
  end
  return signature
end

local function MergeAuraUpdate(target, updateInfo)
  for _, data in ipairs(updateInfo.addedAuras or EMPTY_TABLE) do
    local auraInstanceID = data and data.auraInstanceID
    if not auraInstanceID then return false end
    target.added[auraInstanceID] = data
    target.updated[auraInstanceID] = nil
    target.removed[auraInstanceID] = nil
  end
  for _, auraInstanceID in ipairs(updateInfo.updatedAuraInstanceIDs or EMPTY_TABLE) do
    if not target.added[auraInstanceID] and not target.removed[auraInstanceID] then
      target.updated[auraInstanceID] = true
    end
  end
  for _, auraInstanceID in ipairs(updateInfo.removedAuraInstanceIDs or EMPTY_TABLE) do
    target.added[auraInstanceID] = nil
    target.updated[auraInstanceID] = nil
    target.removed[auraInstanceID] = true
  end
  return true
end

local function IterateAuraUpdate(update, direct, mapKey, listKey)
  if direct then return ipairs(update[listKey] or EMPTY_TABLE) end
  return pairs(update[mapKey] or EMPTY_TABLE)
end

local function ApplyIncrementalAuraUpdate(unit, update, direct)
  local value = caches.aura[unit]
  if not value or type(value.modernByInstance) ~= "table" then return false end
  if SafeCall(UnitGUID, unit) ~= value.unitGUID then return false end
  if SafeCall(UnitName, unit) ~= value.unitName then return false end
  local lookup = C_UnitAuras and C_UnitAuras.GetAuraDataByAuraInstanceID
  local structuralChange = false
  local changedEntries
  local deltaEligible = true
  for key, item in IterateAuraUpdate(update, direct, "removed", "removedAuraInstanceIDs") do
    deltaEligible = false
    local auraInstanceID = direct and item or key
    if value.modernByInstance[auraInstanceID] then structuralChange = true end
    value.modernByInstance[auraInstanceID] = nil
    stats.incrementalAuraRemoves = stats.incrementalAuraRemoves + 1
  end
  for key, item in IterateAuraUpdate(update, direct, "updated", "updatedAuraInstanceIDs") do
    local auraInstanceID = direct and item or key
    local existing = value.modernByInstance[auraInstanceID]
    if type(lookup) ~= "function" then return false end
    local data = SafeCall(lookup, unit, auraInstanceID)
    if not data then return false end
    local filter = AuraFilter(data, existing)
    if not filter then return false end
    if existing then
      if existing.filter ~= filter or not existing.data
        or existing.data.spellId ~= data.spellId
      then
        structuralChange = true
      end
      existing.data, existing.filter = data, filter
      changedEntries = changedEntries or {}
      changedEntries[#changedEntries + 1] = existing
      stats.incrementalAuraChanges = stats.incrementalAuraChanges + 1
    elseif WantsAuraFilter(unit, filter) then
      return false
    else
      deltaEligible = false
      stats.ignoredIncrementalAuras = stats.ignoredIncrementalAuras + 1
    end
  end
  for key, data in IterateAuraUpdate(update, direct, "added", "addedAuras") do
    deltaEligible = false
    local auraInstanceID = direct and data.auraInstanceID or key
    local existing = value.modernByInstance[auraInstanceID]
    local filter = AuraFilter(data, existing)
    if not filter then return false end
    if WantsAuraFilter(unit, filter) then
      if existing then
        existing.data, existing.filter = data, filter
        existing.normalized = nil
      else
        value.modernOrder = (value.modernOrder or 0) + 1
        value.modernByInstance[auraInstanceID] = {
          data = data, filter = filter, order = value.modernOrder,
        }
      end
      structuralChange = true
      stats.incrementalAuraAdds = stats.incrementalAuraAdds + 1
    else
      stats.ignoredIncrementalAuras = stats.ignoredIncrementalAuras + 1
    end
  end
  -- Added, removed, or filter/spell-changed auras do not carry a trustworthy
  -- native list position on this client. Reordering them with modernOrder
  -- would change UnitAura index semantics for WA/Cell/nameplates. Force one
  -- authoritative C_UnitAuras rescan instead; updated-only events still use
  -- the targeted copy-on-write path below.
  if structuralChange then return false end
  generation = generation + 1
  value.generation = generation
  value.updatedAt = type(GetTime) == "function" and GetTime() or 0
  local patched = not structuralChange and PatchUpdatedAuraLists(value, changedEntries)
  local changedCount = changedEntries and #changedEntries or 0
  if changedCount > 0 and not patched then
    -- The update claimed to be non-structural, but its cached legacy index no
    -- longer matches the authoritative APIs. Do not publish a partial delta or
    -- rebuild from the possibly stale instance map; Flush will perform a full
    -- RefreshAura and publish isFullUpdate instead.
    for _, entry in ipairs(changedEntries) do entry.normalized = nil end
    stats.incrementalListRebuilds = stats.incrementalListRebuilds + 1
    return false
  end
  local legacyDelta
  if patched then
    stats.incrementalListPatches = stats.incrementalListPatches + 1
    stats.incrementalListPatchAuras = stats.incrementalListPatchAuras + changedCount
    if deltaEligible and changedCount > 0 then
      table.sort(changedEntries, function(a, b)
        if a.filter ~= b.filter then return a.filter == "HELPFUL" end
        return (a.listIndex or 0) < (b.listIndex or 0)
      end)
      legacyDelta = {
        unitGUID = value.unitGUID,
        structureEpoch = value.structureEpoch,
        snapshotGeneration = value.generation,
      }
      for _, entry in ipairs(changedEntries) do
        legacyDelta[#legacyDelta + 1] = entry.filter
        legacyDelta[#legacyDelta + 1] = entry.listIndex
        legacyDelta[#legacyDelta + 1] = entry.normalized
      end
      stats.legacyAuraDeltaPublishes = stats.legacyAuraDeltaPublishes + 1
      stats.legacyAuraDeltaAuras = stats.legacyAuraDeltaAuras + changedCount
    end
  end
  stats.refreshes = stats.refreshes + 1
  stats.auraRefreshes = stats.auraRefreshes + 1
  stats.incrementalAuraUpdates = stats.incrementalAuraUpdates + 1
  return value, legacyDelta
end

local function QueueAuraUpdate(unit, updateInfo)
  if type(updateInfo) ~= "table" or updateInfo.isFullUpdate then return false end
  if not IsTracked(unit) or not IsKindTracked(unit, "aura") then return false end
  local cached = caches.aura[unit]
  if not cached or type(cached.modernByInstance) ~= "table" then return false end
  local pending = pendingAuraUpdates[unit]
  if not pending then
    local signature = ValidateDirectAuraUpdate(updateInfo)
    if signature then
      pendingAuraUpdates[unit] = updateInfo
      pendingAuraDirect[unit] = true
      pendingAuraSignatures[unit] = signature
      stats.directAuraUpdates = stats.directAuraUpdates + 1
    else
      pending = { added = {}, updated = {}, removed = {} }
      if not MergeAuraUpdate(pending, updateInfo) then return false end
      pendingAuraUpdates[unit] = pending
      pendingAuraDirect[unit] = nil
      pendingAuraSignatures[unit] = nil
      stats.mergedAuraUpdates = stats.mergedAuraUpdates + 1
    end
  else
    stats.coalesced = stats.coalesced + 1
    if pendingAuraDirect[unit] then
      local merged = { added = {}, updated = {}, removed = {} }
      if not MergeAuraUpdate(merged, pending) or not MergeAuraUpdate(merged, updateInfo) then return false end
      pendingAuraUpdates[unit] = merged
      pendingAuraDirect[unit] = nil
      pendingAuraSignatures[unit] = nil
      stats.mergedAuraUpdates = stats.mergedAuraUpdates + 1
    elseif not MergeAuraUpdate(pending, updateInfo) then
      return false
    end
  end
  stats.dirtyMarks = stats.dirtyMarks + 1
  flushPending = true
  if EnsureFlushDriver then EnsureFlushDriver() end
  return true
end

-- Titan/Mists WeakAuras consumes the legacy UnitAura branch.  Building a
-- modern snapshot and then translating it back into legacy tuples costs more
-- than the one native scan WA must perform anyway, and index identity is not
-- guaranteed on this client.  Keep the useful part of centralization here:
-- coalesce all UNIT_AURA notifications for one unit in the same frame, then
-- let WA read the authoritative native API in its unchanged scan routine.
local function QueueAuraSignal(unit, updateInfo)
  if type(unit) ~= "string"
    or type(Hub.HasSubscribers) ~= "function"
    or not Hub.HasSubscribers("aura.event")
  then return false end
  if pendingAuraSignals[unit] then
    stats.auraSignalsCoalesced = stats.auraSignalsCoalesced + 1
    -- Multiple notifications for one unit in the same frame collapse only
    -- after forcing a full native WA refresh.  Trying to merge arbitrary
    -- add/update/remove sequences could change clone and legacy-index
    -- semantics, while a full marker is display-equivalent to native WA.
    pendingAuraSignalUpdates[unit] = FULL_AURA_SIGNAL
    stats.auraSignalFullFallbacks = stats.auraSignalFullFallbacks + 1
  else
    pendingAuraSignals[unit] = true
    stats.auraSignalsQueued = stats.auraSignalsQueued + 1
    if type(updateInfo) == "table" and not updateInfo.isFullUpdate then
      -- Keep one untouched Blizzard updateInfo table as a read-only hint.
      -- WA continues to own all aura state and native API reads.
      pendingAuraSignalUpdates[unit] = updateInfo
      stats.auraSignalIncrementalHints = stats.auraSignalIncrementalHints + 1
    else
      pendingAuraSignalUpdates[unit] = FULL_AURA_SIGNAL
      stats.auraSignalFullFallbacks = stats.auraSignalFullFallbacks + 1
    end
  end
  flushPending = true
  if EnsureFlushDriver then EnsureFlushDriver() end
  return true
end

local function RefreshHealth(unit)
  generation = generation + 1
  local exists = type(UnitExists) ~= "function" or UnitExists(unit)
  local value = {
    unit = unit, generation = generation, exists = exists and true or false,
    health = exists and (SafeCall(UnitHealth, unit) or 0) or 0,
    healthMax = exists and (SafeCall(UnitHealthMax, unit) or 0) or 0,
    updatedAt = type(GetTime) == "function" and GetTime() or 0,
  }
  caches.health[unit] = value
  stats.refreshes, stats.healthRefreshes = stats.refreshes + 1, stats.healthRefreshes + 1
  return value
end

local function RefreshCast(unit)
  generation = generation + 1
  local name, text, texture, startTimeMS, endTimeMS, tradeSkill, castId, notInterruptible, spellId =
    SafeCall(UnitCastingInfo, unit)
  local channel = false
  if not name then
    name, text, texture, startTimeMS, endTimeMS, tradeSkill, notInterruptible, spellId =
      SafeCall(UnitChannelInfo, unit)
    channel = name ~= nil
  end
  local value = {
    unit = unit, generation = generation, active = name ~= nil, channel = channel,
    name = name, text = text, texture = texture, startTimeMS = startTimeMS,
    endTimeMS = endTimeMS, castId = castId, spellId = spellId,
    notInterruptible = notInterruptible and true or false,
    updatedAt = type(GetTime) == "function" and GetTime() or 0,
  }
  caches.cast[unit] = value
  stats.refreshes, stats.castRefreshes = stats.refreshes + 1, stats.castRefreshes + 1
  return value
end

local refreshers = { unit = RefreshUnit, health = RefreshHealth, aura = RefreshAura, cast = RefreshCast }

local function GetSnapshot(kind, unit)
  if not IsTracked(unit) then return nil end
  stats.queries = stats.queries + 1
  if caches[kind][unit] and not dirty[kind][unit] then
    stats.cacheHits = stats.cacheHits + 1
    return kind == "aura" and EnsureAuraLists(caches[kind][unit]) or caches[kind][unit]
  end
  stats.cacheMisses = stats.cacheMisses + 1
  dirty[kind][unit] = nil
  local value = refreshers[kind](unit)
  return kind == "aura" and EnsureAuraLists(value) or value
end

local function Publish(kind, unit, value, updateInfo, providerMetadata)
  if type(Hub.PublishProvider) == "function" then
    local topic = kind .. ".snapshot"
    if kind == "aura"
      and (type(Hub.HasSubscribers) ~= "function" or Hub.HasSubscribers(topic))
    then
      EnsureAuraLists(value)
    end
    Hub.PublishProvider(topic, value, unit, updateInfo, providerMetadata)
    stats.publishes = stats.publishes + 1
  end
end

local function BuildAuraUpdateInfo(update)
  local info = {
    isFullUpdate = false,
    addedAuras = {},
    updatedAuraInstanceIDs = {},
    removedAuraInstanceIDs = {},
  }
  for _, data in pairs(update.added or {}) do
    info.addedAuras[#info.addedAuras + 1] = data
  end
  for auraInstanceID in pairs(update.updated or {}) do
    info.updatedAuraInstanceIDs[#info.updatedAuraInstanceIDs + 1] = auraInstanceID
  end
  for auraInstanceID in pairs(update.removed or {}) do
    info.removedAuraInstanceIDs[#info.removedAuraInstanceIDs + 1] = auraInstanceID
  end
  return info
end

local function ClearPendingAuraUpdate(unit)
  pendingAuraUpdates[unit] = nil
  pendingAuraDirect[unit] = nil
  pendingAuraSignatures[unit] = nil
end

local function HasPendingWork()
  if next(pendingAuraSignals) then return true end
  if next(pendingAuraUpdates) then return true end
  for _, pending in pairs(dirty) do if next(pending) then return true end end
  return false
end

local function CountPendingWork()
  local count = 0
  for _ in pairs(pendingAuraSignals) do count = count + 1 end
  for _, pending in pairs(dirty) do for _ in pairs(pending) do count = count + 1 end end
  for unit in pairs(pendingAuraUpdates) do
    if not dirty.aura[unit] then count = count + 1 end
  end
  return count
end

local function FlushAuraSignals(budget)
  local work = 0
  for unit in pairs(pendingAuraSignals) do
    if work >= budget then break end
    pendingAuraSignals[unit] = nil
    local updateInfo = pendingAuraSignalUpdates[unit] or FULL_AURA_SIGNAL
    pendingAuraSignalUpdates[unit] = nil
    if type(Hub.PublishProvider) == "function" then
      Hub.PublishProvider("aura.event", EMPTY_TABLE, unit, updateInfo)
      stats.publishes = stats.publishes + 1
      stats.auraSignalsPublished = stats.auraSignalsPublished + 1
    end
    work = work + 1
  end
  return work
end

local function FlushKind(kind, budget)
  local work = 0
  local pending = dirty[kind]
  for unit in pairs(pending) do
    if work >= budget then break end
    pending[unit] = nil
    if IsTracked(unit) then
      if kind == "aura" then ClearPendingAuraUpdate(unit) end
      Publish(kind, unit, refreshers[kind](unit), kind == "aura" and { isFullUpdate = true } or nil)
      work = work + 1
    end
  end
  return work
end

local function FlushIncrementalAuras(budget)
  local work = 0
  for unit, update in pairs(pendingAuraUpdates) do
    if work >= budget then break end
    if not dirty.aura[unit] then
      local direct = pendingAuraDirect[unit]
      local expectedSignature = pendingAuraSignatures[unit]
      ClearPendingAuraUpdate(unit)
      if IsTracked(unit) then
        local signatureValid = not direct
          or (expectedSignature ~= nil and ValidateDirectAuraUpdate(update) == expectedSignature)
        if direct and not signatureValid then
          stats.directAuraMutationFallbacks = stats.directAuraMutationFallbacks + 1
        end
        local value, providerMetadata
        if signatureValid then
          value, providerMetadata = ApplyIncrementalAuraUpdate(unit, update, direct)
        else
          value = false
        end
        local updateInfo = value and (direct and update or BuildAuraUpdateInfo(update))
          or { isFullUpdate = true }
        if not value then
          stats.incrementalAuraFallbacks = stats.incrementalAuraFallbacks + 1
          value = RefreshAura(unit)
        end
        Publish("aura", unit, value, updateInfo, providerMetadata)
        work = work + 1
      end
    end
  end
  return work
end

local function Flush()
  if not flushPending then
    if DisableFlushDriver then DisableFlushDriver() end
    return
  end
  flushPending = false
  stats.flushes = stats.flushes + 1
  local sampleRefresh = type(debugprofilestop) == "function"
    and (stats.flushes % REFRESH_PROFILE_INTERVAL == 1)
  local started = sampleRefresh and debugprofilestop() or 0
  local work = 0
  work = work + FlushAuraSignals(MAX_REFRESHES_PER_FRAME - work)
  work = work + FlushKind("health", MAX_REFRESHES_PER_FRAME - work)
  work = work + FlushKind("cast", MAX_REFRESHES_PER_FRAME - work)
  work = work + FlushIncrementalAuras(MAX_REFRESHES_PER_FRAME - work)
  work = work + FlushKind("aura", MAX_REFRESHES_PER_FRAME - work)
  work = work + FlushKind("unit", MAX_REFRESHES_PER_FRAME - work)
  stats.maxFlushWork = math.max(stats.maxFlushWork, work)
  if HasPendingWork() then
    flushPending = true
    stats.budgetYields = stats.budgetYields + 1
    stats.deferredRefreshes = stats.deferredRefreshes + CountPendingWork()
  end
  if started > 0 then
    stats.refreshMs = stats.refreshMs
      + (debugprofilestop() - started) * REFRESH_PROFILE_INTERVAL
    stats.refreshSamples = stats.refreshSamples + 1
  end
  if not flushPending and DisableFlushDriver then DisableFlushDriver() end
end

local flushDriverActive = false
local function FlushOnUpdate() Flush() end

EnsureFlushDriver = function()
  if flushDriverActive or not flushPending then return end
  flushDriverActive = true
  stats.flushDriverStarts = stats.flushDriverStarts + 1
  eventFrame:SetScript("OnUpdate", FlushOnUpdate)
end

DisableFlushDriver = function()
  if not flushDriverActive then return end
  flushDriverActive = false
  stats.flushDriverStops = stats.flushDriverStops + 1
  eventFrame:SetScript("OnUpdate", nil)
end

local META_EVENTS = {
  PLAYER_ENTERING_WORLD = true,
  GROUP_ROSTER_UPDATE = true,
  PLAYER_TARGET_CHANGED = true,
  PLAYER_FOCUS_CHANGED = true,
  NAME_PLATE_UNIT_ADDED = true,
  NAME_PLATE_UNIT_REMOVED = true,
}

local function AnyKindTracked(kind)
  for _, ownerRequests in pairs(requests) do
    for _, unitRequests in pairs(ownerRequests) do
      if unitRequests[kind] then return true end
    end
  end
  return false
end

local function AuraSignalRequested()
  return type(Hub.HasSubscribers) == "function"
    and Hub.HasSubscribers("aura.event") and true or false
end

local function EventWanted(event)
  local kind = UNIT_EVENTS[event]
  if event == "UNIT_AURA" then
    return vfAuraInvalidationRequested or AuraSignalRequested()
      or AnyKindTracked("aura")
  end
  if kind then return AnyKindTracked(kind) end
  if META_EVENTS[event] then return next(unitOwners) ~= nil end
  return false
end

ReconcileEventRegistrations = function()
  for event in pairs(UNIT_EVENTS) do
    local wanted = EventWanted(event)
    if wanted and not registeredEvents[event] then
      eventFrame:RegisterEvent(event)
      registeredEvents[event] = true
      stats.eventRegistrations = stats.eventRegistrations + 1
    elseif not wanted and registeredEvents[event] then
      eventFrame:UnregisterEvent(event)
      registeredEvents[event] = nil
      stats.eventUnregistrations = stats.eventUnregistrations + 1
    end
  end
  for event in pairs(META_EVENTS) do
    local wanted = EventWanted(event)
    if wanted and not registeredEvents[event] then
      eventFrame:RegisterEvent(event)
      registeredEvents[event] = true
      stats.eventRegistrations = stats.eventRegistrations + 1
    elseif not wanted and registeredEvents[event] then
      eventFrame:UnregisterEvent(event)
      registeredEvents[event] = nil
      stats.eventUnregistrations = stats.eventUnregistrations + 1
    end
  end
end

local function HasAcquiredKind(owner, unit, kind)
  local ownerRequests = requests[owner]
  local unitRequests = ownerRequests and ownerRequests[unit]
  return unitRequests and unitRequests[kind] and true or false
end

local function AcquireKind(owner, unit, kind)
  stats.acquireCalls = stats.acquireCalls + 1
  if HasAcquiredKind(owner, unit, kind) then
    stats.acquireFastHits = stats.acquireFastHits + 1
    return true
  end

  local isNewOwner = requests[owner] == nil
  requests[owner] = requests[owner] or {}
  requests[owner][unit] = requests[owner][unit] or {}
  requests[owner][unit][kind] = true

  local isNewUnit = unitOwners[unit] == nil
  unitOwners[unit] = unitOwners[unit] or {}
  unitOwners[unit][owner] = true
  if isNewOwner then stats.owners = stats.owners + 1 end
  if isNewUnit then CountTracked() end
  if not caches[kind][unit] then MarkDirty(unit, kind) end
  if ReconcileEventRegistrations then ReconcileEventRegistrations() end
  return true
end

function provider.Acquire(owner, unit, fields)
  if type(owner) ~= "string" or owner == "" or type(unit) ~= "string" or unit == "" then return false end
  for kind, enabled in pairs(fields or { unit = true }) do
    if refreshers[kind] and enabled then AcquireKind(owner, unit, kind) end
  end
  return true
end

function provider.AcquireAura(owner, unit, filter)
  local normalized = NormalizeAuraFilter(filter)
  if not normalized then return false end
  owner = owner or "anonymous-aura"
  local filters = auraFilterOwners[unit]
  if filters and filters[normalized] and filters[normalized][owner]
    and HasAcquiredKind(owner, unit, "aura")
  then
    stats.auraAcquireFastHits = stats.auraAcquireFastHits + 1
    stats.acquireCalls = stats.acquireCalls + 1
    stats.acquireFastHits = stats.acquireFastHits + 1
    return true
  end
  auraFilterOwners[unit] = auraFilterOwners[unit] or { HELPFUL = {}, HARMFUL = {} }
  local isNewFilter = not auraFilterOwners[unit][normalized][owner]
  auraFilterOwners[unit][normalized][owner] = true
  AcquireKind(owner, unit, "aura")
  local snapshot = caches.aura[unit]
  if isNewFilter and snapshot then
    local missing = not snapshot.scannedFilters or not snapshot.scannedFilters[normalized]
    if missing and not ExtendAuraFilter(unit, normalized) then MarkDirty(unit, "aura") end
  end
  return true
end

-- The common WA/Cell path asks for the same registered broad filter millions
-- of times. Once all registration and cache invariants are known to hold, keep
-- the exact accounting of AcquireAura + GetSnapshot without repeating their
-- nested table walks. Any incomplete, released, dirty, or extended state falls
-- through to the original functions so their repair and refresh semantics stay
-- authoritative.
local function GetRegisteredAuraSnapshot(owner, unit, filter)
  -- UNIT_AURA has arrived but the coalesced provider flush has not run yet.
  -- Returning the previous "clean" snapshot here would make Cell/WA one
  -- update late. Let legacy callers use the native API until the new snapshot
  -- is published.
  if pendingAuraUpdates[unit] then return nil end
  local filters = auraFilterOwners[unit]
  local filterOwners = filters and filters[filter]
  local owners = unitOwners[unit]
  local ownerRequests = requests[owner]
  local unitRequests = ownerRequests and ownerRequests[unit]
  local snapshot = caches.aura[unit]
  if filterOwners and filterOwners[owner]
    and owners and owners[owner]
    and unitRequests and unitRequests.aura
    and snapshot and not dirty.aura[unit]
  then
    stats.acquireCalls = stats.acquireCalls + 1
    stats.acquireFastHits = stats.acquireFastHits + 1
    stats.auraAcquireFastHits = stats.auraAcquireFastHits + 1
    stats.queries = stats.queries + 1
    stats.cacheHits = stats.cacheHits + 1
    stats.auraSnapshotFastHits = stats.auraSnapshotFastHits + 1
    return EnsureAuraLists(snapshot)
  end
  provider.AcquireAura(owner, unit, filter)
  return GetSnapshot("aura", unit)
end

local function RemoveAuraOwner(owner, unit)
  for trackedUnit, filters in pairs(auraFilterOwners) do
    if not unit or trackedUnit == unit then
      filters.HELPFUL[owner] = nil
      filters.HARMFUL[owner] = nil
      if not next(filters.HELPFUL) and not next(filters.HARMFUL) then
        auraFilterOwners[trackedUnit] = nil
      end
    end
  end
end

local function DropUnit(unit, reason)
  local owners = unitOwners[unit]
  if not owners then return false end
  for owner in pairs(owners) do
    local ownerRequests = requests[owner]
    if ownerRequests then
      ownerRequests[unit] = nil
      if not next(ownerRequests) then
        requests[owner] = nil
        stats.owners = math.max(0, stats.owners - 1)
        stats.releasedOwners = stats.releasedOwners + 1
      end
    end
  end
  unitOwners[unit] = nil
  auraFilterOwners[unit] = nil
  canonicalAuraBlocked[unit] = nil
  caches.unit[unit], caches.health[unit], caches.aura[unit], caches.cast[unit] = nil, nil, nil, nil
  dirty.unit[unit], dirty.health[unit], dirty.aura[unit], dirty.cast[unit] = nil, nil, nil, nil
  ClearPendingAuraUpdate(unit)
  stats.releasedUnits = stats.releasedUnits + 1
  if reason == "nameplate" then stats.nameplateReleases = stats.nameplateReleases + 1 end
  if reason == "roster" then stats.rosterReleases = stats.rosterReleases + 1 end
  CountTracked()
  if ReconcileEventRegistrations then ReconcileEventRegistrations() end
  return true
end

function provider.Release(owner, unit)
  local ownerRequests = requests[owner]
  if not ownerRequests then return end
  RemoveAuraOwner(owner, unit)
  if unit then ownerRequests[unit] = nil else requests[owner] = nil end
  for trackedUnit, owners in pairs(unitOwners) do
    if not unit or trackedUnit == unit then
      owners[owner] = nil
      if not next(owners) then
        unitOwners[trackedUnit] = nil
        canonicalAuraBlocked[trackedUnit] = nil
        caches.unit[trackedUnit], caches.health[trackedUnit], caches.aura[trackedUnit], caches.cast[trackedUnit] = nil, nil, nil, nil
        dirty.unit[trackedUnit], dirty.health[trackedUnit], dirty.aura[trackedUnit], dirty.cast[trackedUnit] = nil, nil, nil, nil
        ClearPendingAuraUpdate(trackedUnit)
      end
    end
  end
  if not requests[owner] or not next(requests[owner]) then
    requests[owner] = nil
    stats.owners = math.max(0, stats.owners - 1)
  end
  CountTracked()
  if ReconcileEventRegistrations then ReconcileEventRegistrations() end
end
function provider.ReleaseUnit(unit, reason) return DropUnit(unit, reason) end

function provider.GetUnit(unit) return GetSnapshot("unit", unit) end
function provider.GetHealth(owner, unit)
  if type(unit) ~= "string" then return false end
  AcquireKind(owner or "anonymous-health", unit, "health")
  local snapshot = GetSnapshot("health", unit)
  if not snapshot then return false end
  return true, snapshot.health, snapshot.healthMax
end
function provider.GetAuras(unit)
  if pendingAuraUpdates[unit] then return nil end
  return GetSnapshot("aura", unit)
end
function provider.GetCast(unit) return GetSnapshot("cast", unit) end
local function GetSafeLegacyAuraList(snapshot, filter)
  if type(snapshot) ~= "table"
    or not (snapshot.legacyIdentitySafe and snapshot.legacyIdentitySafe[filter])
  then return nil end
  local list = filter == "HARMFUL" and snapshot.legacyHarmful or snapshot.legacyHelpful
  return type(list) == "table" and list or nil
end
local function ReturnLegacyAura(aura)
  return true, aura.name, aura.icon, aura.stacks, aura.debuffClass, aura.duration,
    aura.expirationTime, aura.unitCaster, aura.isStealable,
    aura.nameplateShowPersonal, aura.spellId, aura.canApplyAura,
    aura.isBossDebuff, aura.isCastByPlayer, aura.nameplateShowAll,
    aura.modRate, aura.point1
end
function provider.GetAuraByIndex(owner, unit, index, filter)
  if type(unit) ~= "string" or type(index) ~= "number" then return false end
  local normalizedFilter = ShareableAuraFilter(filter or "HELPFUL")
  if not normalizedFilter then return false end
  local snapshot = GetRegisteredAuraSnapshot(
    owner or "anonymous-aura", unit, normalizedFilter)
  if not snapshot then return false end
  local list = GetSafeLegacyAuraList(snapshot, normalizedFilter)
  if not list then return false end
  local aura = list[index]
  if not aura then return true end
  return ReturnLegacyAura(aura)
end
function provider.GetUnitAuras(owner, unit, filter)
  if type(unit) ~= "string" or type(filter) ~= "string" then return false end
  local normalizedFilter = ShareableAuraFilter(filter)
  if not normalizedFilter then return false end
  local snapshot = GetRegisteredAuraSnapshot(
    owner or "anonymous-modern-aura", unit, normalizedFilter)
  if not snapshot then return false end
  local list = normalizedFilter == "HARMFUL" and snapshot.modernHarmful or snapshot.modernHelpful
  if list == nil then return false end
  return true, list
end
function provider.GetAuraList(owner, unit, filter)
  if type(unit) ~= "string" or type(filter) ~= "string" then return false end
  local normalizedFilter = ShareableAuraFilter(filter)
  if not normalizedFilter then return false end
  local snapshot = GetRegisteredAuraSnapshot(
    owner or "anonymous-aura-list", unit, normalizedFilter)
  if not snapshot then return false end
  if not (snapshot.legacyIdentitySafe
    and snapshot.legacyIdentitySafe[normalizedFilter])
  then return false end
  local list = normalizedFilter == "HARMFUL" and snapshot.harmful or snapshot.helpful
  if type(list) ~= "table" then return false end
  return true, list
end
function provider.GetLegacyAuraList(owner, unit, filter)
  stats.legacyAuraListQueries = stats.legacyAuraListQueries + 1
  if type(unit) ~= "string" or type(filter) ~= "string" then
    stats.legacyAuraListFallbacks = stats.legacyAuraListFallbacks + 1
    return false
  end
  local normalizedFilter = ShareableAuraFilter(filter)
  if not normalizedFilter then
    stats.legacyAuraListFallbacks = stats.legacyAuraListFallbacks + 1
    return false
  end
  local snapshot = GetRegisteredAuraSnapshot(
    owner or "anonymous-legacy-aura-list", unit, normalizedFilter)
  if not snapshot then
    stats.legacyAuraListFallbacks = stats.legacyAuraListFallbacks + 1
    return false
  end
  local list = GetSafeLegacyAuraList(snapshot, normalizedFilter)
  if not list then
    stats.legacyAuraListFallbacks = stats.legacyAuraListFallbacks + 1
    return false
  end
  stats.legacyAuraListHits = stats.legacyAuraListHits + 1
  return true, list
end
function provider.GetAuraBySpellId(owner, unit, filter, spellId)
  if type(unit) ~= "string" or type(filter) ~= "string" or spellId == nil then return false end
  local normalizedFilter = ShareableAuraFilter(filter)
  if not normalizedFilter then return false end
  local snapshot = GetRegisteredAuraSnapshot(
    owner or "anonymous-aura-spell", unit, normalizedFilter)
  if not snapshot then return false end
  local list = GetSafeLegacyAuraList(snapshot, normalizedFilter)
  if not list then return false end
  for _, aura in ipairs(list) do
    if aura.spellId == spellId then return ReturnLegacyAura(aura) end
  end
  -- A cached miss is not authoritative: CLEU can arrive before UNIT_AURA has
  -- refreshed this snapshot. Let the caller query the native API so newly
  -- applied auras keep their original timing and return values.
  return false
end
function provider.GetAuraBySpellOrName(owner, unit, filter, spell)
  if type(unit) ~= "string" or type(filter) ~= "string"
    or (type(spell) ~= "number" and type(spell) ~= "string")
  then return false end
  local normalizedFilter = ShareableAuraFilter(filter)
  if not normalizedFilter then return false end
  stats.auraSpellQueries = stats.auraSpellQueries + 1
  local snapshot = GetRegisteredAuraSnapshot(
    owner or "anonymous-aura-spell-name", unit, normalizedFilter)
  if not snapshot then return false end
  local list = GetSafeLegacyAuraList(snapshot, normalizedFilter)
  if not list then return false end
  for _, aura in ipairs(list) do
    if spell == aura.spellId or spell == aura.name then
      stats.auraSpellHits = stats.auraSpellHits + 1
      return ReturnLegacyAura(aura)
    end
  end
  stats.auraSpellMisses = stats.auraSpellMisses + 1
  -- Preserve native WA/Cell semantics during the short CLEU -> UNIT_AURA
  -- ordering window. A stale shared snapshot must never hide a fresh aura.
  return false
end
function provider.GetAuraDataByInstanceID(owner, unit, auraInstanceID)
  if type(unit) ~= "string" or auraInstanceID == nil then return false end
  stats.auraInstanceQueries = stats.auraInstanceQueries + 1
  if pendingAuraUpdates[unit] then
    stats.auraInstanceFallbacks = stats.auraInstanceFallbacks + 1
    return false
  end
  AcquireKind(owner or "anonymous-aura-instance", unit, "aura")
  local snapshot = GetSnapshot("aura", unit)
  if not snapshot or type(snapshot.modernByInstance) ~= "table" then
    stats.auraInstanceFallbacks = stats.auraInstanceFallbacks + 1
    return false
  end
  local entry = snapshot.modernByInstance[auraInstanceID]
  if entry then
    stats.auraInstanceHits = stats.auraInstanceHits + 1
    return true, entry.data
  end
  local scanned = snapshot.scannedFilters or {}
  if scanned.HELPFUL and scanned.HARMFUL then return true end
  stats.auraInstanceFallbacks = stats.auraInstanceFallbacks + 1
  return false
end
function provider.GetCastingInfo(owner, unit)
  if type(unit) ~= "string" then return false end
  AcquireKind(owner or "anonymous-cast", unit, "cast")
  local cast = GetSnapshot("cast", unit)
  if not cast then return false end
  if not cast.active or cast.channel then return true end
  return true, cast.name, cast.text, cast.texture, cast.startTimeMS, cast.endTimeMS,
    false, cast.castId, cast.notInterruptible, cast.spellId
end
function provider.GetChannelInfo(owner, unit)
  if type(unit) ~= "string" then return false end
  AcquireKind(owner or "anonymous-cast", unit, "cast")
  local cast = GetSnapshot("cast", unit)
  if not cast then return false end
  if not cast.active or not cast.channel then return true end
  return true, cast.name, cast.text, cast.texture, cast.startTimeMS, cast.endTimeMS,
    false, cast.notInterruptible, cast.spellId
end
function provider.MarkDirty(unit, kind)
  if refreshers[kind] then MarkDirty(unit, kind) end
end
function provider.ReportAuraParityFailure(unit, filter)
  if type(unit) ~= "string"
    or (filter ~= "HELPFUL" and filter ~= "HARMFUL")
  then return false end
  BlockCanonicalAuraFilter(unit, filter)
  local snapshot = caches.aura[unit]
  if snapshot then
    snapshot.legacyIdentitySafe = snapshot.legacyIdentitySafe or {}
    snapshot.legacyIdentitySafe[filter] = false
  end
  ClearPendingAuraUpdate(unit)
  MarkDirty(unit, "aura")
  stats.legacyAuraParityFailures = stats.legacyAuraParityFailures + 1
  return true
end
function provider.Flush() Flush() end
function provider.GetStats() return Copy(stats) end
function provider.SetVFAuraInvalidation(enabled)
  vfAuraInvalidationRequested = enabled and true or false
  if ReconcileEventRegistrations then ReconcileEventRegistrations() end
  return true
end
function provider.IsEventRegistered(event)
  return registeredEvents[event] and true or false
end

-- Allocation-free counters for the low-overhead raid flight recorder.  The
-- returned values are cumulative; the recorder stores one-second deltas.
function provider.GetFlightRecorderStats()
  return stats.events or 0,
    stats.refreshes or 0,
    stats.auraRefreshes or 0,
    stats.fullAuraUpdates or 0,
    stats.incrementalAuraUpdates or 0,
    stats.modernAuraScans or 0,
    stats.legacyAuraScans or 0,
    stats.publishes or 0,
    stats.refreshMs or 0,
    stats.flushes or 0,
    stats.budgetYields or 0,
    stats.deferredRefreshes or 0
end

Hub.UnitDataProvider = provider
Hub.AcquireUnitData = provider.Acquire
Hub.AcquireAuraData = provider.AcquireAura
Hub.ReleaseUnitData = provider.Release
Hub.ReleaseUnitDataToken = provider.ReleaseUnit
Hub.GetUnitSnapshot = provider.GetUnit
Hub.GetHealth = provider.GetHealth
Hub.GetAuraSnapshot = provider.GetAuras
Hub.GetCastSnapshot = provider.GetCast
Hub.GetAuraByIndex = provider.GetAuraByIndex
Hub.GetUnitAuras = provider.GetUnitAuras
Hub.GetAuraList = provider.GetAuraList
Hub.GetLegacyAuraList = provider.GetLegacyAuraList
Hub.GetAuraBySpellId = provider.GetAuraBySpellId
Hub.GetAuraBySpellOrName = provider.GetAuraBySpellOrName
Hub.GetAuraDataByInstanceID = provider.GetAuraDataByInstanceID
Hub.ReportAuraParityFailure = provider.ReportAuraParityFailure
Hub.GetCastingInfo = provider.GetCastingInfo
Hub.GetChannelInfo = provider.GetChannelInfo
Hub.GetUnitDataStats = provider.GetStats
Hub.GetUnitDataFlightRecorderStats = provider.GetFlightRecorderStats
Hub.SubscribeAuraSignal = function(owner, callback)
  if type(Hub.Subscribe) ~= "function" then return false end
  local result = Hub.Subscribe(owner, "aura.event", callback)
  if ReconcileEventRegistrations then ReconcileEventRegistrations() end
  return result
end
Hub.UnsubscribeAuraSignal = function(owner)
  if type(Hub.Unsubscribe) ~= "function" then return false end
  Hub.Unsubscribe(owner, "aura.event")
  if ReconcileEventRegistrations then ReconcileEventRegistrations() end
  return true
end
Hub.SetUnitDataVFAuraInvalidation = provider.SetVFAuraInvalidation
Hub.IsUnitDataEventRegistered = provider.IsEventRegistered

eventFrame:SetScript("OnEvent", function(_, event, unit, updateInfo)
  stats.events = stats.events + 1
  local kind = UNIT_EVENTS[event]
  if kind and unit then
    if event == "UNIT_AURA" then
      if vfAuraInvalidationRequested
        and type(Hub.InvalidateVFAuraUnit) == "function"
      then
        Hub.InvalidateVFAuraUnit(unit)
      end
      QueueAuraSignal(unit, updateInfo)
      if QueueAuraUpdate(unit, updateInfo) then return end
    end
    MarkDirty(unit, kind)
    return
  end
  if event == "NAME_PLATE_UNIT_REMOVED" then
    if unit then DropUnit(unit, "nameplate") end
    return
  end
  if event == "NAME_PLATE_UNIT_ADDED" then
    if unit then
      MarkDirty(unit, "unit"); MarkDirty(unit, "health"); MarkDirty(unit, "aura"); MarkDirty(unit, "cast")
    end
    return
  end
  if event == "GROUP_ROSTER_UPDATE" then
    local departed = {}
    for trackedUnit in pairs(unitOwners) do
      if (trackedUnit:match("^raid%d+$") or trackedUnit:match("^party%d+$"))
        and type(UnitExists) == "function" and not UnitExists(trackedUnit)
      then
        departed[#departed + 1] = trackedUnit
      end
    end
    for _, departedUnit in ipairs(departed) do DropUnit(departedUnit, "roster") end
  end
  for trackedUnit in pairs(unitOwners) do
    MarkDirty(trackedUnit, "unit"); MarkDirty(trackedUnit, "health"); MarkDirty(trackedUnit, "aura"); MarkDirty(trackedUnit, "cast")
  end
end)

ReconcileEventRegistrations()
