local ADDON = "RaidOptimizerHub"
local VERSION = "0.5.49"
local WA_FRAME_RESOLVER_MAJOR = "RaidOptimizerHub-WeakAuras-LibGetFrame-1.0"
local WA_FRAME_RESOLVER_BUILD = "wa-5.21.8-lgf74-private2"
local WA_FRAME_RESOLVER_MINOR = 74
local WA_FRAME_RESOLVER_SITES = { "core", "auraEnvironment", "profiling", "dynamicGroup" }

RaidOptimizerHubDB = RaidOptimizerHubDB or {}
RaidOptimizerHub = RaidOptimizerHub or {}

local db
local frame = CreateFrame("Frame")
local subscriptions = {}
local cleuRoutes = { wildcard = {}, bySubevent = {}, staticCount = 0 }
local EMPTY_CLEU_ROUTE = {}
local cleuRoutesDirty = true
local waBatchGuards = {}
local snapshots = {}
local boundFrames = {}
local tickSubscribers = {}
local demandsByOwner = {}
local demandTotals = {}
local knownDemandCategories = {}
local combatDemandBroad = {}
local combatDemandSpells = {}
local combatDemandSpellNames = {}
local sessionStartAddonMetrics
local CollectAddonMetrics
local combatWindowStartMetrics
local combatWindowStartedAt
local combatWindowLastEventAt
local combatWindowActive = false
local combatWindowAddonCpu = {}
local subeventCounts = {}
local frameTimeHistogram = {}
local sessionFrameTimeHistogram = {}
local zoneFrameStats = {}
local currentZoneKey = "unknown"
local currentZoneName = ""
local currentInstanceID = 0
local currentIsZulGurub = false
local sessionFrameStride = 0
local memorySampleElapsed = 0
local lastLuaMemoryKB
local locationSamples = {}
local locationSampleElapsed = 0
local locationSampleFrames = 0
local locationSampleWorstMs = 0
local locationHeartbeat = 0
local LOCATION_SAMPLE_LIMIT = 240
local LOCATION_LOW_FPS = 95
local LIGHT_PROFILE_SAMPLE_INTERVAL = 512
local lightProfileActive = false
local lightProfileDownstreamMs = 0
local waLoadedDisplays = {}
local waLoadedInventory = {}
local METRIC_ADDONS = {
  "Details", "WeakAuras", "Platynator", "Plater", "Cell", "ElvUI", "NDui",
  "RaidOptimizerHub", "RaidOptimizerDetailsLabBridge", "RaidOptimizerDetailsCore",
}
local stats = {
  received = 0,
  dispatched = 0,
  callbackErrors = 0,
  quarantined = 0,
  nativeCombatReads = 0,
  cleuDispatches = 0,
  nativeEventPayloadSkips = 0,
  lastSecond = 0,
  rate = 0,
  peakRate = 0,
  decodeMs = 0,
  damageInputEvents = 0,
  ownerInputEvents = 0,
  nativeBypassBindings = 0,
  nativeObservedEvents = 0,
  combatWindowEvents = 0,
  combatWindowSeconds = 0,
  combatWindows = 0,
  demandUpdates = 0,
  tickSubscribers = 0,
  tickDispatches = 0,
  tickErrors = 0,
  frameSamples = 0,
  frameTimeMs = 0,
  worstFrameMs = 0,
  framesOver33Ms = 0,
  framesOver50Ms = 0,
  framesOver100Ms = 0,
  sessionFrameSamples = 0,
  sessionFrameTimeMs = 0,
  sessionWorstFrameMs = 0,
  sessionFramesOver33Ms = 0,
  sessionFramesOver50Ms = 0,
  sessionFramesOver100Ms = 0,
  gcChecks = 0,
  gcDrops = 0,
  gcDropKB = 0,
  largestGcDropKB = 0,
  luaMemoryKB = 0,
  peakLuaMemoryKB = 0,
  filterChecks = 0,
  filtered = 0,
  profileSamples = 0,
  providerPublishes = 0,
  providerPublishSkips = 0,
  routedFilterSkips = 0,
  routeBuilds = 0,
  routeCandidates = 0,
  emptyRouteReuses = 0,
  compiledDemandChecks = 0,
  compiledDemandSkips = 0,
  combatMetricCollectionsSkipped = 0,
  waBatchCalls = 0,
  waBatchGroups = 0,
  waBatchDisplays = 0,
  waBatchScopes = {},
  waBatchRecoveries = 0,
  waBatchRecoveryErrors = 0,
  waLegacyAuraDeltaAttempts = 0,
  waLegacyAuraDeltaHits = 0,
  waLegacyAuraDeltaFallbacks = 0,
  waLegacyAuraDeltaAuras = 0,
  waCleuVisibilityDeferrals = 0,
  waCleuVisibilityReleases = 0,
  waCleuVisibilityTimeouts = 0,
  waCleuVisibilityErrors = 0,
  waCleuVisibilityMaxQueue = 0,
  waCleuVisibilityFastRejects = 0,
  waCleuVisibilityPayloadAllocations = 0,
  waCleuVisibilityPayloadReuses = 0,
  waCleuVisibilityEntryAllocations = 0,
  waCleuVisibilityEntryReuses = 0,
  vfDecisions = 0,
  vfQueries = 0,
  vfCacheHits = 0,
  vfAuraCacheHits = 0,
  vfCrossDecisionHits = 0,
  vfNegativeStores = 0,
  vfDecisionEntryReuses = 0,
  vfNegativeEntryReuses = 0,
  vfAuraInvalidations = 0,
  vfNativeCalls = 0,
  vfAdapterInstalls = 0,
  vfAdapterDisables = 0,
  vfObservedDecisions = 0,
  vfAdapterErrors = 0,
  vfRegistrationHooks = 0,
  vfPrepareCalls = 0,
  vfTargetResolves = 0,
  vfTargetMisses = 0,
  vfRegistrationCalls = 0,
  vfCallbacksWrapped = 0,
  vfCallbacksRestored = 0,
  lightProfileSamples = 0,
  lightProfileTotalMs = 0,
  lightProfileDownstreamMs = 0,
  lightProfileHubMs = 0,
  waLoadBatches = 0,
  waUnloadBatches = 0,
  waLoadedDisplayCount = 0,
  locationSamplesCaptured = 0,
  startedAt = time(),
}
-- Profiling is diagnostic only. Sampling less often reduces observer overhead
-- without changing any consumer callback or display semantics.
local PROFILE_SAMPLE_INTERVAL = 128
local secondBucket = 0
local bucketEvents = 0
local function ResolveRuntimeMode()
  local config = RaidOptimizerHubRuntimeConfig
  return type(config) == "table" and config.freeMode == true and config.mode == "central"
    and "central" or "native"
end
local runtimeMode = ResolveRuntimeMode()
-- The standalone black-box recorder owns performance diagnosis. The customer
-- runtime should spend its frame budget sharing data, not measuring itself.
-- Developers can opt diagnostics back in explicitly without changing any
-- consumer or display path.
local diagnosticsEnabled = runtimeMode ~= "central"
  or RaidOptimizerHubDB.diagnostics == true

-- Known rotation packages built on VF_Common expose a public APL registration
-- interface and read-only state helpers. Keep the author's implementation
-- authoritative on the first query. Exact results can be reused inside one
-- synchronous APL callback; only confirmed aura absence may survive until the
-- corresponding unit's next native aura-change signal.
local vfDecisionDepth = 0
local vfDecisionSerial = 0
local vfDecisionTokens = {}
local vfDecisionQueries = {}
local vfDecisionHits = {}
local vfDecisionAuraHits = {}
local vfAuraCaches = {}
local vfWrappedCallbacks = setmetatable({}, { __mode = "k" })
local vfWrappedAPL = setmetatable({}, { __mode = "k" })
local vfRegistration = { target = nil, original = nil, wrapper = nil, disabled = false }
local vfNegativeAuraCache = { getBuff = {}, getDebuff = {} }
local vfAuraGenerations = { player = 0, target = 0, focus = 0 }
local vfAuraUnitsByGUID = {}
local vfAuraInvalidationEnabled = false
local vfAuraInvalidationViaProvider = false
local VF_NEGATIVE_CACHE_UNITS = { player = true, target = true, focus = true }
local VF_AURA_CHANGE_EVENTS = {
  SPELL_AURA_APPLIED = true,
  SPELL_AURA_REMOVED = true,
  SPELL_AURA_REFRESH = true,
  SPELL_AURA_APPLIED_DOSE = true,
  SPELL_AURA_REMOVED_DOSE = true,
  SPELL_AURA_BROKEN = true,
  SPELL_AURA_BROKEN_SPELL = true,
  SPELL_DISPEL = true,
  SPELL_STOLEN = true,
}
local vfCandidateAuras = {
  ["VF_Core4.3"] = true,
  ["VF_HekiliFollower"] = true,
  ["VF_Interface"] = true,
  ["XT&AQ_暗牧"] = true,
  ["七叔_武器战"] = true,
  ["七叔_深血"] = true,
  ["七叔_狂暴战"] = true,
  ["五月&XT&如夏_惩戒骑"] = true,
  ["五月狂飙_生存猎"] = true,
  ["凌小猫粮_暗牧"] = true,
  ["凌小猫粮_毁伤"] = true,
  ["凌小猫粮_轨道炮"] = true,
  ["夜雨_双手增强萨"] = true,
  ["影舞索妮娅_敏锐贼"] = true,
  ["海滩泣露_踏风_forMOP"] = true,
  ["猫咪猛挠_forMOP"] = true,
  ["猫咪猛挠_forWLK"] = true,
  ["疯狂&念秋_天打邪"] = true,
  ["竹井詩織里&斧王_奶萨"] = true,
  ["竹井詩織里&斧王_戒律牧"] = true,
  ["竹井詩織里_奶德"] = true,
  ["竹井詩織里_奶骑"] = true,
  ["米娅_毁灭术"] = true,
  ["虎呗特_兽王猎"] = true,
  ["虎呗特_碎冰法"] = true,
}

local function VFInvalidateAuraUnit(unit)
  if not VF_NEGATIVE_CACHE_UNITS[unit] then return end
  vfAuraGenerations[unit] = (vfAuraGenerations[unit] or 0) + 1
  -- Entries carry this generation. Keep their bounded table structure so a
  -- proc-heavy fight does not discard and rebuild the same unit/spell/filter
  -- tables on every UNIT_AURA; stale values can never match the new number.
  stats.vfAuraInvalidations = stats.vfAuraInvalidations + 1
end

function RaidOptimizerHub.InvalidateVFAuraUnit(unit)
  if not vfAuraInvalidationEnabled then return false end
  VFInvalidateAuraUnit(unit)
  return true
end

local function VFRefreshAuraGUIDMap()
  for guid in pairs(vfAuraUnitsByGUID) do vfAuraUnitsByGUID[guid] = nil end
  if type(UnitGUID) ~= "function" then return end
  for unit in pairs(VF_NEGATIVE_CACHE_UNITS) do
    local guid = UnitGUID(unit)
    if guid then
      local units = vfAuraUnitsByGUID[guid]
      if not units then units = {}; vfAuraUnitsByGUID[guid] = units end
      units[unit] = true
    end
  end
end

local function VFInvalidateAuraGUID(guid)
  local units = guid and vfAuraUnitsByGUID[guid]
  if not units then return end
  for unit in pairs(units) do VFInvalidateAuraUnit(unit) end
end

local function VFEnableAuraInvalidation()
  if vfAuraInvalidationEnabled then return end
  vfAuraInvalidationEnabled = true
  if type(RaidOptimizerHub.SetUnitDataVFAuraInvalidation) == "function" then
    local ok, accepted = pcall(RaidOptimizerHub.SetUnitDataVFAuraInvalidation, true)
    vfAuraInvalidationViaProvider = ok and accepted and true or false
  end
  if not vfAuraInvalidationViaProvider then frame:RegisterEvent("UNIT_AURA") end
  frame:RegisterEvent("PLAYER_TARGET_CHANGED")
  frame:RegisterEvent("PLAYER_FOCUS_CHANGED")
  VFRefreshAuraGUIDMap()
end

local function VFDisableAuraInvalidation()
  if not vfAuraInvalidationEnabled then return end
  vfAuraInvalidationEnabled = false
  if vfAuraInvalidationViaProvider
    and type(RaidOptimizerHub.SetUnitDataVFAuraInvalidation) == "function"
  then
    pcall(RaidOptimizerHub.SetUnitDataVFAuraInvalidation, false)
  else
    frame:UnregisterEvent("UNIT_AURA")
  end
  vfAuraInvalidationViaProvider = false
  frame:UnregisterEvent("PLAYER_TARGET_CHANGED")
  frame:UnregisterEvent("PLAYER_FOCUS_CHANGED")
  vfNegativeAuraCache.getBuff = {}
  vfNegativeAuraCache.getDebuff = {}
  for guid in pairs(vfAuraUnitsByGUID) do vfAuraUnitsByGUID[guid] = nil end
end
local vfAdapter = {
  target = nil,
  originals = {},
  wrappers = {},
  persistent = false,
  disabled = false,
  observedDecisions = 0,
  observedQueries = 0,
  observedHits = 0,
  observedAuraHits = 0,
}

local function VFRestoreWrappedCallbacks()
  for aplEnvironment, entry in pairs(vfWrappedAPL) do
    if type(aplEnvironment) == "table" and type(entry) == "table" then
      if rawget(aplEnvironment, "APLCallback") == entry.wrapper then
        rawset(aplEnvironment, "APLCallback", entry.original)
        stats.vfCallbacksRestored = stats.vfCallbacksRestored + 1
      end
    end
    vfWrappedAPL[aplEnvironment] = nil
  end
end

local function VFCurrentDecision()
  if vfDecisionDepth <= 0 then return nil end
  return vfDecisionDepth, vfDecisionTokens[vfDecisionDepth]
end

local function VFCanCache()
  return runtimeMode == "central" and vfDecisionDepth > 0
end

local function VFAuraRead(method, unit, spell, filter, ...)
  local original = vfAdapter.originals[method]
  if type(original) ~= "function" then return nil end
  if not VFCanCache() or select("#", ...) ~= 0
    or type(unit) ~= "string"
    or (type(spell) ~= "number" and type(spell) ~= "string")
    or (filter ~= nil and type(filter) ~= "string")
  then
    return original(unit, spell, filter, ...)
  end
  local depth, generation = VFCurrentDecision()
  local depthCache = vfAuraCaches[depth]
  if not depthCache then
    depthCache = { getSpellCD = {}, getItemCD = {}, getBuff = {}, getDebuff = {} }
    vfAuraCaches[depth] = depthCache
  end
  local methodCache = depthCache[method]
  local unitCache = methodCache[unit]
  if not unitCache then unitCache = {}; methodCache[unit] = unitCache end
  local spellCache = unitCache[spell]
  if not spellCache then spellCache = {}; unitCache[spell] = spellCache end
  local filterKey = filter or false
  local entry = spellCache[filterKey]
  stats.vfQueries = stats.vfQueries + 1
  vfDecisionQueries[depth] = vfDecisionQueries[depth] + 1
  if entry and entry.generation == generation then
    stats.vfCacheHits = stats.vfCacheHits + 1
    stats.vfAuraCacheHits = stats.vfAuraCacheHits + 1
    vfDecisionHits[depth] = vfDecisionHits[depth] + 1
    vfDecisionAuraHits[depth] = vfDecisionAuraHits[depth] + 1
    return entry.first, entry.second
  end
  if VF_NEGATIVE_CACHE_UNITS[unit] then
    local negativeUnit = vfNegativeAuraCache[method][unit]
    local negativeSpell = negativeUnit and negativeUnit[spell]
    local negative = negativeSpell and negativeSpell[filterKey]
    if negative and negative.generation == vfAuraGenerations[unit] then
      stats.vfCacheHits = stats.vfCacheHits + 1
      stats.vfAuraCacheHits = stats.vfAuraCacheHits + 1
      stats.vfCrossDecisionHits = stats.vfCrossDecisionHits + 1
      vfDecisionHits[depth] = vfDecisionHits[depth] + 1
      vfDecisionAuraHits[depth] = vfDecisionAuraHits[depth] + 1
      if entry then
        entry.generation = generation
        entry.first = negative.first
        entry.second = negative.second
        stats.vfDecisionEntryReuses = stats.vfDecisionEntryReuses + 1
      else
        spellCache[filterKey] = {
          generation = generation,
          first = negative.first,
          second = negative.second,
        }
      end
      return negative.first, negative.second
    end
  end
  stats.vfNativeCalls = stats.vfNativeCalls + 1
  local first, second = original(unit, spell, filter)
  if entry then
    entry.generation = generation
    entry.first = first
    entry.second = second
  else
    spellCache[filterKey] = { generation = generation, first = first, second = second }
  end
  if VF_NEGATIVE_CACHE_UNITS[unit] then
    local negativeMethod = vfNegativeAuraCache[method]
    local negativeUnit = negativeMethod[unit]
    if not negativeUnit then negativeUnit = {}; negativeMethod[unit] = negativeUnit end
    local negativeSpell = negativeUnit[spell]
    if not negativeSpell then negativeSpell = {}; negativeUnit[spell] = negativeSpell end
    if first == 0 and (second == nil or second == 0) then
      local negative = negativeSpell[filterKey]
      if negative then
        negative.generation = vfAuraGenerations[unit]
        negative.first = first
        negative.second = second
        stats.vfNegativeEntryReuses = stats.vfNegativeEntryReuses + 1
      else
        negativeSpell[filterKey] = {
          generation = vfAuraGenerations[unit],
          first = first,
          second = second,
        }
      end
      stats.vfNegativeStores = stats.vfNegativeStores + 1
    end
  end
  return first, second
end

local function VFScalarRead(method, key, ...)
  local original = vfAdapter.originals[method]
  if type(original) ~= "function" then return nil end
  if not VFCanCache() or select("#", ...) ~= 0
    or (type(key) ~= "number" and type(key) ~= "string")
  then
    return original(key, ...)
  end
  local depth, generation = VFCurrentDecision()
  local depthCache = vfAuraCaches[depth]
  if not depthCache then
    depthCache = { getSpellCD = {}, getItemCD = {}, getBuff = {}, getDebuff = {} }
    vfAuraCaches[depth] = depthCache
  end
  local methodCache = depthCache[method]
  local entry = methodCache[key]
  stats.vfQueries = stats.vfQueries + 1
  vfDecisionQueries[depth] = vfDecisionQueries[depth] + 1
  if entry and entry.generation == generation then
    stats.vfCacheHits = stats.vfCacheHits + 1
    vfDecisionHits[depth] = vfDecisionHits[depth] + 1
    return entry.value
  end
  stats.vfNativeCalls = stats.vfNativeCalls + 1
  local value = original(key)
  if entry then
    entry.generation = generation
    entry.value = value
  else
    methodCache[key] = { generation = generation, value = value }
  end
  return value
end

vfAdapter.wrappers.getSpellCD = function(...) return VFScalarRead("getSpellCD", ...) end
vfAdapter.wrappers.getItemCD = function(...) return VFScalarRead("getItemCD", ...) end
vfAdapter.wrappers.getBuff = function(...) return VFAuraRead("getBuff", ...) end
vfAdapter.wrappers.getDebuff = function(...) return VFAuraRead("getDebuff", ...) end

local function VFResolveAdapterTarget(environment)
  -- VF_Interface documents the public VF_* functions as forwarding shells.
  -- The actual shared channel is VF_Common, whose registerAPL/get* methods are
  -- used by every class module.  Target that table directly so obfuscated core
  -- initialisation and WeakAuras environment placement do not change semantics.
  local common
  if type(rawget(environment, "registerAPL")) == "function" then
    common = environment
  else
    common = rawget(environment, "VF_Common")
    if common == nil then common = environment.VF_Common end
  end
  if type(common) ~= "table"
    or type(rawget(common, "registerAPL")) ~= "function"
    or type(rawget(common, "getSpellCD")) ~= "function"
    or type(rawget(common, "getItemCD")) ~= "function"
    or type(rawget(common, "getBuff")) ~= "function"
    or type(rawget(common, "getDebuff")) ~= "function"
  then
    stats.vfTargetMisses = stats.vfTargetMisses + 1
    return nil
  end
  stats.vfTargetResolves = stats.vfTargetResolves + 1
  return common
end

local function VFRestoreAdapter(force)
  local target = vfAdapter.target
  if type(target) ~= "table" then return true end
  if vfAdapter.persistent and not force then return true end
  local safe = true
  for method, wrapper in pairs(vfAdapter.wrappers) do
    local current = rawget(target, method)
    if current == wrapper then
      rawset(target, method, vfAdapter.originals[method])
    elseif current ~= vfAdapter.originals[method] then
      -- Another addon changed the same public function during this call. Do
      -- not overwrite its newer value; fail closed and stop future wrapping.
      safe = false
    end
  end
  vfAdapter.target = nil
  vfAdapter.originals = {}
  vfAdapter.persistent = false
  if not safe then
    vfAdapter.disabled = true
    stats.vfAdapterErrors = stats.vfAdapterErrors + 1
    RaidOptimizerHub.BeginVFDecision = nil
  end
  if vfAdapter.disabled then
    VFRestoreWrappedCallbacks()
    VFDisableAuraInvalidation()
  end
  return safe
end

local function VFInstallAdapter(environment)
  if runtimeMode ~= "central" or type(environment) ~= "table" then return false end
  local target = VFResolveAdapterTarget(environment)
  if type(target) ~= "table" or vfAdapter.disabled then return false end

  if vfAdapter.persistent then
    if vfAdapter.target ~= target then return false end
    for method, wrapper in pairs(vfAdapter.wrappers) do
      if rawget(target, method) ~= wrapper then
        vfAdapter.disabled = true
        stats.vfAdapterErrors = stats.vfAdapterErrors + 1
        VFRestoreAdapter(true)
        return false
      end
    end
    return true
  end

  -- The package's class modules call the public VF_get* globals.  Wrap those
  -- exact entry points only for the synchronous APL/trigger call. Nested calls
  -- share the outer scope and the final End restores the originals.
  if vfDecisionDepth > 0 then
    if vfAdapter.target ~= target then return false end
    for method, wrapper in pairs(vfAdapter.wrappers) do
      if rawget(target, method) ~= wrapper then return false end
    end
    return true
  end

  local originals = {}
  for method, wrapper in pairs(vfAdapter.wrappers) do
    local current = rawget(target, method)
    if current == wrapper or type(current) ~= "function" then return false end
    originals[method] = current
  end
  vfAdapter.target = target
  vfAdapter.originals = originals
  for method, wrapper in pairs(vfAdapter.wrappers) do
    rawset(target, method, wrapper)
  end
  stats.vfAdapterInstalls = stats.vfAdapterInstalls + 1
  return true
end

local function VFInstallPersistentAdapter(target)
  if type(target) ~= "table" or vfAdapter.disabled then return false end
  if vfAdapter.persistent then return vfAdapter.target == target end
  if vfAdapter.target ~= nil or vfDecisionDepth > 0 then return false end
  local originals = {}
  for method, wrapper in pairs(vfAdapter.wrappers) do
    local current = rawget(target, method)
    if current == wrapper or type(current) ~= "function" then return false end
    originals[method] = current
  end
  vfAdapter.target = target
  vfAdapter.originals = originals
  vfAdapter.persistent = true
  for method, wrapper in pairs(vfAdapter.wrappers) do
    rawset(target, method, wrapper)
  end
  stats.vfAdapterInstalls = stats.vfAdapterInstalls + 1
  return true
end

function RaidOptimizerHub.BeginVFDecision(environment, auraId, triggerNumber, event)
  if runtimeMode ~= "central" or not vfCandidateAuras[auraId] then return nil end
  local ok, installed = pcall(VFInstallAdapter, environment)
  if not ok or not installed then
    if not ok then stats.vfAdapterErrors = stats.vfAdapterErrors + 1 end
    return nil
  end
  vfDecisionDepth = vfDecisionDepth + 1
  vfDecisionSerial = vfDecisionSerial + 1
  local decision = vfDecisionSerial
  vfDecisionTokens[vfDecisionDepth] = decision
  vfDecisionQueries[vfDecisionDepth] = 0
  vfDecisionHits[vfDecisionDepth] = 0
  vfDecisionAuraHits[vfDecisionDepth] = 0
  stats.vfDecisions = stats.vfDecisions + 1
  return decision
end

local function VFBeginAPLDecision(environment)
  if runtimeMode ~= "central" or vfAdapter.disabled then return nil end
  local ok, installed = pcall(VFInstallAdapter, environment)
  if not ok or not installed then
    if not ok then stats.vfAdapterErrors = stats.vfAdapterErrors + 1 end
    return nil
  end
  vfDecisionDepth = vfDecisionDepth + 1
  vfDecisionSerial = vfDecisionSerial + 1
  local decision = vfDecisionSerial
  vfDecisionTokens[vfDecisionDepth] = decision
  vfDecisionQueries[vfDecisionDepth] = 0
  vfDecisionHits[vfDecisionDepth] = 0
  vfDecisionAuraHits[vfDecisionDepth] = 0
  stats.vfDecisions = stats.vfDecisions + 1
  return decision
end

function RaidOptimizerHub.EndVFDecision(decision)
  if decision == nil then return true end
  local depth = vfDecisionDepth
  if depth > 0 and vfDecisionTokens[depth] == decision then
    local queries = vfDecisionQueries[depth]
    local hits = vfDecisionHits[depth]
    local auraHits = vfDecisionAuraHits[depth]
    vfDecisionTokens[depth] = nil
    vfDecisionQueries[depth] = nil
    vfDecisionHits[depth] = nil
    vfDecisionAuraHits[depth] = nil
    vfDecisionDepth = depth - 1
    if queries > 0 then
      vfAdapter.observedDecisions = vfAdapter.observedDecisions + 1
      vfAdapter.observedQueries = vfAdapter.observedQueries + queries
      vfAdapter.observedHits = vfAdapter.observedHits + hits
      vfAdapter.observedAuraHits = vfAdapter.observedAuraHits + auraHits
      stats.vfObservedDecisions = stats.vfObservedDecisions + 1
      -- Cheap/unique-query APLs can cost more to memoize than to read. After
      -- a short warm-up, remove every wrapper when fewer than one in five reads
      -- was actually duplicated. A reload starts a fresh, class-specific
      -- sample; no user configuration is persisted or changed.
      if vfAdapter.observedDecisions >= 64
        and vfAdapter.observedHits * 5 < vfAdapter.observedQueries
      then
        vfAdapter.disabled = true
        stats.vfAdapterDisables = stats.vfAdapterDisables + 1
        VFRestoreAdapter(true)
        -- GenericTrigger checks this entry before doing any environment work.
        -- Registered APL wrappers also observe disabled and call the author's
        -- callback directly, so the long-term path remains native-safe.
        RaidOptimizerHub.BeginVFDecision = nil
      end
    end
    if vfDecisionDepth == 0 then VFRestoreAdapter() end
    return true
  end
  -- A mismatched/nested callback must never leave a cache active outside the
  -- author's trigger execution. Fail closed by discarding the complete stack.
  for index = depth, 1, -1 do
    vfDecisionTokens[index] = nil
    vfDecisionQueries[index] = nil
    vfDecisionHits[index] = nil
    vfDecisionAuraHits[index] = nil
    vfAuraCaches[index] = nil
  end
  vfDecisionDepth = 0
  stats.vfAdapterErrors = stats.vfAdapterErrors + 1
  VFRestoreAdapter(true)
  return false
end

local function VFWrapAPLCallback(environment, aplEnvironment)
  if type(aplEnvironment) ~= "table" then return false end
  local original = rawget(aplEnvironment, "APLCallback")
  if type(original) ~= "function" then return false end
  if vfWrappedCallbacks[original] then return true end
  local wrapper
  wrapper = function(...)
    local decision = VFBeginAPLDecision(environment)
    if decision == nil then return original(...) end
    local ok, first, second, third, fourth = xpcall(original, function(err) return err end, ...)
    RaidOptimizerHub.EndVFDecision(decision)
    if not ok then error(first, 0) end
    return first, second, third, fourth
  end
  vfWrappedCallbacks[wrapper] = original
  vfWrappedAPL[aplEnvironment] = { original = original, wrapper = wrapper }
  rawset(aplEnvironment, "APLCallback", wrapper)
  stats.vfCallbacksWrapped = stats.vfCallbacksWrapped + 1
  return true
end

function RaidOptimizerHub.PrepareVFRegistration(environment, auraId)
  stats.vfPrepareCalls = stats.vfPrepareCalls + 1
  if runtimeMode ~= "central" or not vfCandidateAuras[auraId]
    or type(environment) ~= "table" or vfRegistration.disabled
  then
    return false
  end
  local target = VFResolveAdapterTarget(environment)
  if type(target) ~= "table" then return false end
  local current = rawget(target, "registerAPL")
  if vfRegistration.target then
    if target == vfRegistration.target and current == vfRegistration.wrapper then return true end
    vfRegistration.disabled = true
    stats.vfAdapterErrors = stats.vfAdapterErrors + 1
    return false
  end
  if type(current) ~= "function" then return false end
  local wrapper = function(aplEnvironment, ...)
    stats.vfRegistrationCalls = stats.vfRegistrationCalls + 1
    VFWrapAPLCallback(target, aplEnvironment)
    return current(aplEnvironment, ...)
  end
  vfRegistration.target = target
  vfRegistration.original = current
  vfRegistration.wrapper = wrapper
  rawset(target, "registerAPL", wrapper)
  stats.vfRegistrationHooks = stats.vfRegistrationHooks + 1
  VFEnableAuraInvalidation()
  return VFInstallPersistentAdapter(target)
end

if runtimeMode ~= "central" then
  RaidOptimizerHub.BeginVFDecision = nil
  RaidOptimizerHub.EndVFDecision = nil
  RaidOptimizerHub.PrepareVFRegistration = nil
end

-- Current CLEU values live only for the synchronous hub dispatch stack. This
-- keeps old consumers compatible without allocating a full event table.
local currentActive = false
local currentEventTime
local c01, c02, c03, c04, c05, c06, c07, c08, c09, c10, c11, c12, c13
local c14, c15, c16, c17, c18, c19, c20, c21, c22, c23, c24, c25

local DAMAGE_EVENTS = {
  SWING_DAMAGE = true,
  RANGE_DAMAGE = true,
  SPELL_DAMAGE = true,
  SPELL_PERIODIC_DAMAGE = true,
  SPELL_BUILDING_DAMAGE = true,
  DAMAGE_SHIELD = true,
  DAMAGE_SPLIT = true,
  ENVIRONMENTAL_DAMAGE = true,
}

local HEAL_EVENTS = {
  SPELL_HEAL = true,
  SPELL_PERIODIC_HEAL = true,
}

-- Customer runtime is intentionally silent; failures remain in internal stats
-- and always fall back to the original game/addon path.
local function Print(_) end

local function CopyTable(source)
  local result = {}
  for key, value in pairs(source or {}) do result[key] = value end
  return result
end

local function UpdateZoneContext()
  local name, instanceID = "", 0
  if type(GetInstanceInfo) == "function" then
    local ok, zoneName, _, _, _, _, _, _, zoneInstanceID = pcall(GetInstanceInfo)
    if ok then
      name = tostring(zoneName or "")
      instanceID = tonumber(zoneInstanceID) or 0
    end
  elseif type(GetRealZoneText) == "function" then
    name = tostring(GetRealZoneText() or "")
  end
  currentZoneName = name
  currentInstanceID = instanceID
  currentIsZulGurub = instanceID == 309
    or name == "祖尔格拉布"
    or name == "Zul'Gurub"
  currentZoneKey = tostring(instanceID) .. ":" .. (name ~= "" and name or "unknown")
  zoneFrameStats[currentZoneKey] = zoneFrameStats[currentZoneKey] or {
    name = name,
    instanceID = instanceID,
    isZulGurub = currentIsZulGurub,
    samples = 0,
    frameTimeMs = 0,
    worstFrameMs = 0,
    framesOver33Ms = 0,
    framesOver50Ms = 0,
    framesOver100Ms = 0,
  }
end

local function SampleSessionFrame(elapsed)
  sessionFrameStride = sessionFrameStride + 1
  if sessionFrameStride % 2 ~= 0 then return end
  local frameMs = math.max(0, tonumber(elapsed) or 0) * 1000
  if frameMs <= 0 then return end
  stats.sessionFrameSamples = stats.sessionFrameSamples + 1
  stats.sessionFrameTimeMs = stats.sessionFrameTimeMs + frameMs
  stats.sessionWorstFrameMs = math.max(stats.sessionWorstFrameMs, frameMs)
  if frameMs >= 33.34 then stats.sessionFramesOver33Ms = stats.sessionFramesOver33Ms + 1 end
  if frameMs >= 50 then stats.sessionFramesOver50Ms = stats.sessionFramesOver50Ms + 1 end
  if frameMs >= 100 then stats.sessionFramesOver100Ms = stats.sessionFramesOver100Ms + 1 end
  local bucket = math.min(200, math.max(1, math.floor(frameMs) + 1))
  sessionFrameTimeHistogram[bucket] = (sessionFrameTimeHistogram[bucket] or 0) + 1
  local zone = zoneFrameStats[currentZoneKey]
  if not zone then UpdateZoneContext(); zone = zoneFrameStats[currentZoneKey] end
  if zone then
    zone.samples = zone.samples + 1
    zone.frameTimeMs = zone.frameTimeMs + frameMs
    zone.worstFrameMs = math.max(zone.worstFrameMs, frameMs)
    if frameMs >= 33.34 then zone.framesOver33Ms = zone.framesOver33Ms + 1 end
    if frameMs >= 50 then zone.framesOver50Ms = zone.framesOver50Ms + 1 end
    if frameMs >= 100 then zone.framesOver100Ms = zone.framesOver100Ms + 1 end
  end
end

local function SampleLuaMemory(elapsed)
  memorySampleElapsed = memorySampleElapsed + (tonumber(elapsed) or 0)
  if memorySampleElapsed < 1 or type(collectgarbage) ~= "function" then return end
  memorySampleElapsed = memorySampleElapsed % 1
  local ok, value = pcall(collectgarbage, "count")
  local current = ok and tonumber(value) or nil
  if not current then return end
  stats.gcChecks = stats.gcChecks + 1
  stats.luaMemoryKB = current
  stats.peakLuaMemoryKB = math.max(stats.peakLuaMemoryKB, current)
  if lastLuaMemoryKB and lastLuaMemoryKB - current >= 512 then
    local drop = lastLuaMemoryKB - current
    stats.gcDrops = stats.gcDrops + 1
    stats.gcDropKB = stats.gcDropKB + drop
    stats.largestGcDropKB = math.max(stats.largestGcDropKB, drop)
  end
  lastLuaMemoryKB = current
end

-- Low-FPS location samples are deliberately coarse (one-second windows) so
-- diagnosis does not become another per-frame workload. Healthy periods only
-- keep a ten-second heartbeat; sustained drops keep one sample per second.
local function CaptureLocationSample(windowSeconds, frames, worstMs)
  if windowSeconds <= 0 or frames <= 0 then return end
  local fps = frames / windowSeconds
  locationHeartbeat = locationHeartbeat + windowSeconds
  if fps >= LOCATION_LOW_FPS and locationHeartbeat < 10 then return end
  locationHeartbeat = 0

  local sample = {
    time = time(),
    fps = fps,
    worstFrameMs = worstMs,
    zone = currentZoneName,
    instanceID = currentInstanceID,
    subZone = type(GetSubZoneText) == "function" and GetSubZoneText() or "",
    inCombat = type(UnitAffectingCombat) == "function" and UnitAffectingCombat("player") and true or false,
    groupSize = type(GetNumGroupMembers) == "function" and GetNumGroupMembers() or 0,
    eventRate = stats.rate,
    luaMemoryKB = stats.luaMemoryKB,
    gcDrops = stats.gcDrops,
  }

  if C_NamePlate and type(C_NamePlate.GetNamePlates) == "function" then
    local ok, plates = pcall(C_NamePlate.GetNamePlates)
    sample.visibleNameplates = ok and type(plates) == "table" and #plates or 0
  end
  if type(GetNetStats) == "function" then
    local ok, _, _, home, world = pcall(GetNetStats)
    if ok then sample.homeLatencyMs, sample.worldLatencyMs = home or 0, world or 0 end
  end
  if C_Map and type(C_Map.GetBestMapForUnit) == "function" and type(C_Map.GetPlayerMapPosition) == "function" then
    local okMap, mapID = pcall(C_Map.GetBestMapForUnit, "player")
    if okMap and mapID then
      sample.mapID = mapID
      local okPosition, position = pcall(C_Map.GetPlayerMapPosition, mapID, "player")
      if okPosition and position and type(position.GetXY) == "function" then
        local okXY, x, y = pcall(position.GetXY, position)
        if okXY then sample.x, sample.y = x, y end
      end
    end
  end

  locationSamples[#locationSamples + 1] = sample
  if #locationSamples > LOCATION_SAMPLE_LIMIT then table.remove(locationSamples, 1) end
  stats.locationSamplesCaptured = stats.locationSamplesCaptured + 1
end

local function SampleLocationWindow(elapsed)
  elapsed = math.max(0, tonumber(elapsed) or 0)
  locationSampleElapsed = locationSampleElapsed + elapsed
  locationSampleFrames = locationSampleFrames + 1
  locationSampleWorstMs = math.max(locationSampleWorstMs, elapsed * 1000)
  if locationSampleElapsed < 1 then return end
  CaptureLocationSample(locationSampleElapsed, locationSampleFrames, locationSampleWorstMs)
  locationSampleElapsed, locationSampleFrames, locationSampleWorstMs = 0, 0, 0
end

local function HasSubscribers(topic)
  local topicSubscribers = subscriptions[topic]
  return topicSubscribers and next(topicSubscribers) ~= nil
end

local function NeedsCombatLog()
  if runtimeMode ~= "central" then return false end
  return HasSubscribers("combat.raw")
    or HasSubscribers("combat.cleu.input")
    or HasSubscribers("combat.damage")
    or HasSubscribers("combat.damage.input")
    or HasSubscribers("combat.owner.input")
    or HasSubscribers("combat.heal")
    or HasSubscribers("combat.rate")
    or HasSubscribers("combat.pressure")
end

local function ReconcileEvents()
  if runtimeMode == "native" then
    -- Lab-only workload counter: do not decode CLEU or forward it in the
    -- native baseline, but retain a comparable event-density measurement.
    frame:RegisterEvent("COMBAT_LOG_EVENT_UNFILTERED")
    return
  end
  if NeedsCombatLog() then
    frame:RegisterEvent("COMBAT_LOG_EVENT_UNFILTERED")
  else
    frame:UnregisterEvent("COMBAT_LOG_EVENT_UNFILTERED")
  end
end

local function DispatchEntry(owner, entry, ...)
  if not entry.disabled then
      entry.seen = entry.seen + 1
      local allowed = true
      local compiledDemandKnown = false
      local demandCategory = entry.options.combatDemandCategory
      if demandCategory and type(RaidOptimizerHub.HasCombatLogDemand) == "function" then
        local wanted = RaidOptimizerHub.HasCombatLogDemand(
          demandCategory, c02, c12, c13)
        if wanted ~= nil then
          compiledDemandKnown = true
          allowed = wanted and true or false
          stats.compiledDemandChecks = stats.compiledDemandChecks + 1
          if not allowed then stats.compiledDemandSkips = stats.compiledDemandSkips + 1 end
        end
      end
      if not compiledDemandKnown and type(entry.options.filter) == "function" then
        stats.filterChecks = stats.filterChecks + 1
        local sampleFilter = diagnosticsEnabled and debugprofilestop
          and (entry.seen % PROFILE_SAMPLE_INTERVAL == 1)
        local filterStarted = sampleFilter and debugprofilestop() or 0
        local ok, result = xpcall(entry.options.filter, geterrorhandler(), ...)
        if filterStarted > 0 then
          entry.filterMs = entry.filterMs
            + (debugprofilestop() - filterStarted) * PROFILE_SAMPLE_INTERVAL
          entry.filterSamples = entry.filterSamples + 1
          stats.profileSamples = stats.profileSamples + 1
        end
        if not ok then
          stats.callbackErrors = stats.callbackErrors + 1
          result = true
        end
        allowed = result ~= false
      end
      if not allowed then
        entry.filtered = entry.filtered + 1
        stats.filtered = stats.filtered + 1
      else
      local now = currentEventTime or GetTime()
      if entry.windowStart == 0 or now - entry.windowStart > 30 then
        entry.windowStart = now
        entry.errors = 0
      end
      local sampleCallback = diagnosticsEnabled and debugprofilestop
        and (entry.calls % PROFILE_SAMPLE_INTERVAL == 0)
      local started = sampleCallback and debugprofilestop() or 0
      local lightStarted = lightProfileActive and debugprofilestop
        and debugprofilestop() or 0
      local ok
      if entry.options.nativeEventSemantics then
        -- COMBAT_LOG_EVENT_UNFILTERED has no event payload. Patched consumers
        -- read the one shared payload through GetCurrentCombatLogEvent(), just
        -- like they previously read CombatLogGetCurrentEventInfo(). Avoid
        -- repacking 25 values through the callback and the frame handler.
        ok = xpcall(entry.callback, geterrorhandler())
        stats.nativeEventPayloadSkips = stats.nativeEventPayloadSkips + 1
      else
        ok = xpcall(entry.callback, geterrorhandler(), ...)
      end
      if started > 0 then
        entry.workMs = entry.workMs
          + (debugprofilestop() - started) * PROFILE_SAMPLE_INTERVAL
        entry.workSamples = entry.workSamples + 1
        stats.profileSamples = stats.profileSamples + 1
      end
      if lightStarted > 0 then
        local elapsed = debugprofilestop() - lightStarted
        lightProfileDownstreamMs = lightProfileDownstreamMs + elapsed
        entry.lightWorkMs = (entry.lightWorkMs or 0) + elapsed
        entry.lightWorkSamples = (entry.lightWorkSamples or 0) + 1
      end
      entry.calls = entry.calls + 1
      if ok then
        stats.dispatched = stats.dispatched + 1
        entry.delivered = entry.delivered + 1
      else
        stats.callbackErrors = stats.callbackErrors + 1
        entry.errors = entry.errors + 1
        if entry.errors >= 3 then
          entry.disabled = true
          cleuRoutesDirty = true
          stats.quarantined = stats.quarantined + 1
          local activeFrame = entry.options and entry.options.boundFrame and boundFrames[owner]
          if activeFrame and type(activeFrame.RegisterEvent) == "function" then
            -- A bound plugin handed its only native CLEU registration to the
            -- hub.  Quarantining the callback without returning that event
            -- would permanently silence WA/Plater.  Restore the exact native
            -- event first, then detach the failed central binding.
            local restored = pcall(
              activeFrame.RegisterEvent,
              activeFrame,
              "COMBAT_LOG_EVENT_UNFILTERED"
            )
            if restored then
              boundFrames[owner] = nil
              if subscriptions["combat.cleu.input"] then
                subscriptions["combat.cleu.input"][owner] = nil
              end
              cleuRoutesDirty = true
              ReconcileEvents()
              Print("订阅方 " .. tostring(owner) .. " 连续报错，已恢复原生战斗日志事件。")
            else
              Print("订阅方 " .. tostring(owner) .. " 连续报错，原生战斗日志事件恢复失败。")
            end
          else
            Print("订阅方 " .. tostring(owner) .. " 连续报错，已临时隔离。")
          end
        end
      end
      end
  end
end

local function RebuildCleuRoutes()
  local routes = {
    wildcard = {},
    bySubevent = {},
    staticCount = 0,
    payloadRequiredWildcard = false,
    payloadRequiredBySubevent = {},
  }
  for owner, entry in pairs(subscriptions["combat.cleu.input"] or {}) do
    if not entry.disabled then
      local payloadRequired = not entry.options.nativeEventSemantics
        or (type(entry.options.filter) == "function"
          and not entry.options.combatDemandCategory)
      local subevents = entry.options.subevents
      if type(subevents) == "table" and next(subevents) ~= nil then
        routes.staticCount = routes.staticCount + 1
        for subevent, enabled in pairs(subevents) do
          if enabled then
            routes.bySubevent[subevent] = routes.bySubevent[subevent] or {}
            routes.bySubevent[subevent][#routes.bySubevent[subevent] + 1] = { owner, entry }
            if payloadRequired then
              routes.payloadRequiredBySubevent[subevent] = true
            end
          end
        end
      else
        routes.wildcard[#routes.wildcard + 1] = { owner, entry }
        if payloadRequired then routes.payloadRequiredWildcard = true end
      end
    end
  end
  cleuRoutes = routes
  cleuRoutesDirty = false
  stats.routeBuilds = stats.routeBuilds + 1
end

local function DispatchCleu(...)
  if cleuRoutesDirty then RebuildCleuRoutes() end
  local subevent = currentActive and c02 or select(2, ...)
  local routed = cleuRoutes.bySubevent[subevent]
  if not routed then
    routed = EMPTY_CLEU_ROUTE
    stats.emptyRouteReuses = stats.emptyRouteReuses + 1
  end
  local skipped = cleuRoutes.staticCount - #routed
  if skipped > 0 then
    stats.filterChecks = stats.filterChecks + skipped
    stats.routedFilterSkips = stats.routedFilterSkips + skipped
    stats.filtered = stats.filtered + skipped
  end
  stats.routeCandidates = stats.routeCandidates + #cleuRoutes.wildcard + #routed
  for index = 1, #cleuRoutes.wildcard do
    local candidate = cleuRoutes.wildcard[index]
    DispatchEntry(candidate[1], candidate[2], ...)
  end
  for index = 1, #routed do
    local candidate = routed[index]
    DispatchEntry(candidate[1], candidate[2], ...)
  end
end

local function DispatchCurrentCleu()
  if cleuRoutesDirty then RebuildCleuRoutes() end
  if not cleuRoutes.payloadRequiredWildcard
    and not cleuRoutes.payloadRequiredBySubevent[c02]
  then
    DispatchCleu()
  else
    DispatchCleu(RaidOptimizerHub.GetCurrentCombatLogEvent())
  end
end

local function Dispatch(topic, ...)
  local topicSubscribers = subscriptions[topic]
  if not topicSubscribers then return end
  if topic == "combat.cleu.input" then
    DispatchCleu(...)
    return
  end
  for owner, entry in pairs(topicSubscribers) do DispatchEntry(owner, entry, ...) end
end

local function PublishSnapshot(topic, value, ...)
  snapshots[topic] = value
  Dispatch(topic, value, ...)
end

local function FlushSecond(nowSecond)
  if secondBucket == 0 then
    secondBucket = nowSecond
    return
  end
  if nowSecond == secondBucket then return end
  stats.lastSecond = secondBucket
  stats.rate = bucketEvents
  stats.peakRate = math.max(stats.peakRate, bucketEvents)
  if HasSubscribers("combat.rate") or HasSubscribers("combat.pressure") then
    local snapshot = {
      version = VERSION,
      time = secondBucket,
      rate = bucketEvents,
      peakRate = stats.peakRate,
      total = stats.received,
    }
    PublishSnapshot("combat.rate", snapshot)
    PublishSnapshot("combat.pressure", snapshot)
  end
  bucketEvents = 0
  secondBucket = nowSecond
end

local function AmountFromCombatLog(subevent, arg12, arg13, arg15)
  if subevent == "SWING_DAMAGE" then return arg12 or 0 end
  if subevent == "ENVIRONMENTAL_DAMAGE" then return arg13 or 0 end
  return arg15 or 0
end

local function DamageFields(subevent, arg12, arg13, arg15, arg18, arg19, arg21)
  if subevent == "SWING_DAMAGE" then
    return tonumber(arg12) or 0, 6603, arg18 and true or false
  end
  if subevent == "ENVIRONMENTAL_DAMAGE" then
    return tonumber(arg13) or 0, tostring(arg12 or "environment"), arg19 and true or false
  end
  return tonumber(arg15) or 0, tonumber(arg12) or 0, arg21 and true or false
end

local function ClearCurrentCombatLog()
  currentActive = false
  c01, c02, c03, c04, c05, c06, c07, c08, c09, c10, c11, c12, c13 = nil
  c14, c15, c16, c17, c18, c19, c20, c21, c22, c23, c24, c25 = nil
end

-- Some custom WA triggers react to SPELL_AURA_APPLIED and immediately query
-- UnitAura for the same player aura. On the legacy Titan client the combat-log
-- event can arrive one frame before UnitAura exposes duration/expirationTime.
-- Keep WeakAuras' CLEU order intact, never invent aura values, and release the
-- original event as soon as the native aura tuple becomes readable. A short
-- timeout deliberately falls back to the original event semantics.
local WA_CLEU_VISIBILITY_EVENTS = {
  SPELL_AURA_APPLIED = true,
  SPELL_AURA_REFRESH = true,
  SPELL_AURA_APPLIED_DOSE = true,
}
local WA_CLEU_VISIBILITY_TIMEOUT = 0.12
local waCleuVisibilityQueue = {}
local waCleuVisibilityHead = 1
local waCleuVisibilityDrainScheduled = false
local waCleuVisibilityPayloadPool = {}
local waCleuVisibilityEntryPool = {}
local DrainWeakAurasCleuVisibilityQueue

local function WeakAurasPlayerAuraEvent(subevent, destGUID, spellId, auraType)
  if not WA_CLEU_VISIBILITY_EVENTS[subevent] then return false end
  if type(UnitGUID) ~= "function" then return false end
  local playerGUID = UnitGUID("player")
  if not playerGUID or destGUID ~= playerGUID then return false end
  return tonumber(spellId) ~= nil and (auraType == "BUFF" or auraType == "DEBUFF")
end

local function WeakAurasPlayerAuraTupleReadyFields(subevent, destGUID, spellId, auraType)
  if type(UnitGUID) ~= "function" or type(UnitAura) ~= "function" then return true end
  if not WeakAurasPlayerAuraEvent(subevent, destGUID, spellId, auraType) then return true end
  spellId = tonumber(spellId)
  local filter = auraType == "DEBUFF" and "HARMFUL" or "HELPFUL"
  for index = 1, 255 do
    local name, _, _, _, duration, expirationTime, _, _, _, auraSpellId =
      UnitAura("player", index, filter)
    if not name then return false end
    if auraSpellId == spellId then
      return duration ~= nil and expirationTime ~= nil
    end
  end
  return false
end

local function WeakAurasPlayerAuraTupleReady(payload)
  return WeakAurasPlayerAuraTupleReadyFields(
    payload[2], payload[8], payload[12], payload[15])
end

local function AcquireWeakAurasCleuPayload()
  local payload = table.remove(waCleuVisibilityPayloadPool)
  if payload then
    stats.waCleuVisibilityPayloadReuses = (stats.waCleuVisibilityPayloadReuses or 0) + 1
  else
    payload = {}
    stats.waCleuVisibilityPayloadAllocations = (stats.waCleuVisibilityPayloadAllocations or 0) + 1
  end
  payload[1], payload[2], payload[3], payload[4], payload[5] = c01, c02, c03, c04, c05
  payload[6], payload[7], payload[8], payload[9], payload[10] = c06, c07, c08, c09, c10
  payload[11], payload[12], payload[13], payload[14], payload[15] = c11, c12, c13, c14, c15
  payload[16], payload[17], payload[18], payload[19], payload[20] = c16, c17, c18, c19, c20
  payload[21], payload[22], payload[23], payload[24], payload[25] = c21, c22, c23, c24, c25
  return payload
end

local function ReleaseWeakAurasCleuPayload(payload)
  if not payload then return end
  wipe(payload)
  if #waCleuVisibilityPayloadPool < 512 then
    waCleuVisibilityPayloadPool[#waCleuVisibilityPayloadPool + 1] = payload
  end
end

local function AcquireWeakAurasCleuEntry()
  local entry = table.remove(waCleuVisibilityEntryPool)
  if entry then
    stats.waCleuVisibilityEntryReuses = (stats.waCleuVisibilityEntryReuses or 0) + 1
  else
    entry = {}
    stats.waCleuVisibilityEntryAllocations = (stats.waCleuVisibilityEntryAllocations or 0) + 1
  end
  return entry
end

local function ReleaseWeakAurasCleuEntry(entry)
  if not entry then return end
  entry.payload, entry.callback, entry.waitForAura, entry.queuedAt = nil, nil, nil, nil
  if #waCleuVisibilityEntryPool < 512 then
    waCleuVisibilityEntryPool[#waCleuVisibilityEntryPool + 1] = entry
  end
end

local function ScheduleWeakAurasCleuVisibilityDrain(delay)
  if waCleuVisibilityDrainScheduled then return true end
  if not C_Timer or type(C_Timer.After) ~= "function" then return false end
  waCleuVisibilityDrainScheduled = true
  C_Timer.After(delay or 0, DrainWeakAurasCleuVisibilityQueue)
  return true
end

DrainWeakAurasCleuVisibilityQueue = function()
  waCleuVisibilityDrainScheduled = false
  while waCleuVisibilityHead <= #waCleuVisibilityQueue do
    local entry = waCleuVisibilityQueue[waCleuVisibilityHead]
    local payload = entry.payload
    local ready = not entry.waitForAura or WeakAurasPlayerAuraTupleReady(payload)
    local now = GetTime and GetTime() or 0
    if not ready and now - entry.queuedAt < WA_CLEU_VISIBILITY_TIMEOUT then
      ScheduleWeakAurasCleuVisibilityDrain(0.01)
      return
    end
    waCleuVisibilityHead = waCleuVisibilityHead + 1
    if entry.waitForAura then
      if ready then
        stats.waCleuVisibilityReleases = stats.waCleuVisibilityReleases + 1
      else
        stats.waCleuVisibilityTimeouts = stats.waCleuVisibilityTimeouts + 1
      end
    end
    local ok = xpcall(entry.callback, geterrorhandler(), unpack(payload, 1, 25))
    if not ok then
      stats.waCleuVisibilityErrors = stats.waCleuVisibilityErrors + 1
    end
    ReleaseWeakAurasCleuPayload(payload)
    ReleaseWeakAurasCleuEntry(entry)
  end
  wipe(waCleuVisibilityQueue)
  waCleuVisibilityHead = 1
end

function RaidOptimizerHub.DeferWeakAurasCurrentCombatLog(callback)
  if runtimeMode ~= "central" or not currentActive or type(callback) ~= "function" then
    return false
  end
  if not C_Timer or type(C_Timer.After) ~= "function" then return false end
  local hasPending = waCleuVisibilityHead <= #waCleuVisibilityQueue
  local playerAuraEvent = WeakAurasPlayerAuraEvent(c02, c08, c12, c15)
  if not playerAuraEvent and not hasPending then
    stats.waCleuVisibilityFastRejects = (stats.waCleuVisibilityFastRejects or 0) + 1
    return false
  end
  local waitForAura = playerAuraEvent
    and not WeakAurasPlayerAuraTupleReadyFields(c02, c08, c12, c15)
  if not waitForAura and not hasPending then return false end
  local payload = AcquireWeakAurasCleuPayload()
  local entry = AcquireWeakAurasCleuEntry()
  entry.payload = payload
  entry.callback = callback
  entry.waitForAura = waitForAura
  entry.queuedAt = GetTime and GetTime() or 0
  waCleuVisibilityQueue[#waCleuVisibilityQueue + 1] = entry
  stats.waCleuVisibilityDeferrals = stats.waCleuVisibilityDeferrals + 1
  local queueLength = #waCleuVisibilityQueue - waCleuVisibilityHead + 1
  if queueLength > stats.waCleuVisibilityMaxQueue then
    stats.waCleuVisibilityMaxQueue = queueLength
  end
  ScheduleWeakAurasCleuVisibilityDrain(0)
  return true
end

local function ScriptProfilingEnabled()
  if type(GetCVarBool) == "function" then
    local ok, enabled = pcall(GetCVarBool, "scriptProfile")
    if ok then return enabled and true or false end
  end
  if type(GetCVar) == "function" then
    local ok, value = pcall(GetCVar, "scriptProfile")
    if ok then return tostring(value) == "1" end
  end
  return false
end

local function StartCombatWindow(now)
  if combatWindowActive then return end
  combatWindowActive = true
  combatWindowStartedAt = now
  combatWindowLastEventAt = now
  if ScriptProfilingEnabled() and type(CollectAddonMetrics) == "function" then
    combatWindowStartMetrics = CollectAddonMetrics()
  else
    combatWindowStartMetrics = nil
    stats.combatMetricCollectionsSkipped = stats.combatMetricCollectionsSkipped + 1
  end
  stats.combatWindows = stats.combatWindows + 1
end

local function TrackCombatWindowEvent(now)
  now = now or (GetTime and GetTime() or 0)
  if not combatWindowActive and type(UnitAffectingCombat) == "function"
    and not UnitAffectingCombat("player")
  then
    return
  end
  StartCombatWindow(now)
  combatWindowLastEventAt = now
  stats.combatWindowEvents = stats.combatWindowEvents + 1
end

local function FinishCombatWindow(now)
  if not combatWindowActive then return end
  if combatWindowStartMetrics and type(CollectAddonMetrics) == "function" then
    local current = CollectAddonMetrics()
    for addonName, metric in pairs(current) do
      local initial = combatWindowStartMetrics[addonName]
      if initial then
        combatWindowAddonCpu[addonName] = (combatWindowAddonCpu[addonName] or 0)
          + math.max(0, (metric.cpuMs or 0) - (initial.cpuMs or 0))
      end
    end
  end
  stats.combatWindowSeconds = stats.combatWindowSeconds
    + math.max(0, (combatWindowLastEventAt or now) - (combatWindowStartedAt or now))
  combatWindowActive = false
  combatWindowStartMetrics = nil
  combatWindowStartedAt = nil
  combatWindowLastEventAt = nil
end

function RaidOptimizerHub.GetCurrentCombatLogEvent()
  if not currentActive then return nil end
  return c01, c02, c03, c04, c05, c06, c07, c08, c09, c10, c11, c12, c13,
    c14, c15, c16, c17, c18, c19, c20, c21, c22, c23, c24, c25
end

function RaidOptimizerHub.HasCurrentCombatLogEvent()
  return currentActive
end

function RaidOptimizerHub.GetCurrentCombatLogSubevent()
  return currentActive and c02 or nil
end

function RaidOptimizerHub.RecordWABatch(scope, groups, displays)
  scope = type(scope) == "string" and scope or "unknown"
  groups = math.max(0, tonumber(groups) or 0)
  displays = math.max(0, tonumber(displays) or 0)
  stats.waBatchCalls = stats.waBatchCalls + 1
  stats.waBatchGroups = stats.waBatchGroups + groups
  stats.waBatchDisplays = stats.waBatchDisplays + displays
  local bucket = stats.waBatchScopes[scope]
  if not bucket then
    bucket = { calls = 0, groups = 0, displays = 0 }
    stats.waBatchScopes[scope] = bucket
  end
  bucket.calls = bucket.calls + 1
  bucket.groups = bucket.groups + groups
  bucket.displays = bucket.displays + displays
end

function RaidOptimizerHub.RecordWALegacyAuraDelta(used, auraCount)
  stats.waLegacyAuraDeltaAttempts = stats.waLegacyAuraDeltaAttempts + 1
  if used then
    stats.waLegacyAuraDeltaHits = stats.waLegacyAuraDeltaHits + 1
    stats.waLegacyAuraDeltaAuras = stats.waLegacyAuraDeltaAuras
      + math.max(0, tonumber(auraCount) or 0)
  else
    stats.waLegacyAuraDeltaFallbacks = stats.waLegacyAuraDeltaFallbacks + 1
  end
end

function RaidOptimizerHub.ArmWABatchGuard(guard)
  if type(guard) ~= "table" then return false end
  waBatchGuards[guard] = true
  return true
end

function RaidOptimizerHub.DisarmWABatchGuard(guard)
  waBatchGuards[guard] = nil
end

local function RecoverInterruptedWABatches()
  for guard in pairs(waBatchGuards) do
    waBatchGuards[guard] = nil
    stats.waBatchRecoveries = stats.waBatchRecoveries + 1
    for index = 1, #guard do
      local region = guard[index]
      if region and type(region.Resume) == "function" then
        local ok = xpcall(region.Resume, geterrorhandler(), region)
        if not ok then stats.waBatchRecoveryErrors = stats.waBatchRecoveryErrors + 1 end
      end
    end
  end
end

local function ProcessCombatLog(
  timestamp, subevent, hideCaster, sourceGUID, sourceName, sourceFlags, sourceRaidFlags,
  destGUID, destName, destFlags, destRaidFlags, arg12, arg13, arg14, arg15, arg16,
  arg17, arg18, arg19, arg20, arg21, arg22, arg23, arg24, arg25
)
  if not subevent then return end
  local now = GetTime and GetTime() or 0
  currentEventTime = now
  subeventCounts[subevent] = (subeventCounts[subevent] or 0) + 1
  TrackCombatWindowEvent(now)
  local sampleDecode = diagnosticsEnabled and debugprofilestop
    and (stats.received % PROFILE_SAMPLE_INTERVAL == 0)
  local started = sampleDecode and debugprofilestop() or 0
  local lightStarted = runtimeMode == "central" and debugprofilestop
    and (stats.received % LIGHT_PROFILE_SAMPLE_INTERVAL == 0)
    and debugprofilestop() or 0
  lightProfileActive = lightStarted > 0
  lightProfileDownstreamMs = 0
  currentActive = true
  c01, c02, c03, c04, c05, c06, c07, c08, c09, c10, c11, c12, c13 =
    timestamp, subevent, hideCaster, sourceGUID, sourceName, sourceFlags, sourceRaidFlags,
    destGUID, destName, destFlags, destRaidFlags, arg12, arg13
  c14, c15, c16, c17, c18, c19, c20, c21, c22, c23, c24, c25 =
    arg14, arg15, arg16, arg17, arg18, arg19, arg20, arg21, arg22, arg23, arg24, arg25

  local nowSecond = math.floor(now)
  FlushSecond(nowSecond)
  stats.received = stats.received + 1
  bucketEvents = bucketEvents + 1

  -- A cached absence is valid only until the corresponding unit can gain or
  -- lose an aura.  Invalidate before any shared CLEU consumer runs so an APL
  -- triggered by the same combat-log event never observes stale absence.
  if vfAuraInvalidationEnabled and VF_AURA_CHANGE_EVENTS[subevent] then
    VFInvalidateAuraGUID(destGUID)
  end

  if HasSubscribers("combat.cleu.input") then
    DispatchCurrentCleu()
    stats.cleuDispatches = stats.cleuDispatches + 1
  end
  if HasSubscribers("combat.raw") then Dispatch("combat.raw", "COMBAT_LOG_EVENT_UNFILTERED") end

  if DAMAGE_EVENTS[subevent] or HEAL_EVENTS[subevent] then
    local kind = DAMAGE_EVENTS[subevent] and "damage" or "heal"
    if kind == "damage" and HasSubscribers("combat.damage.input") then
      local amount, spellId, critical = DamageFields(subevent, arg12, arg13, arg15, arg18, arg19, arg21)
      Dispatch("combat.damage.input", sourceGUID, sourceName, sourceFlags, destGUID, destName, destFlags, spellId, amount, critical, subevent)
      stats.damageInputEvents = stats.damageInputEvents + 1
    end
    if HasSubscribers("combat." .. kind) then
      local payload = {
        timestamp = timestamp,
        subevent = subevent,
        kind = kind,
        amount = tonumber(AmountFromCombatLog(subevent, arg12, arg13, arg15)) or 0,
        sourceGUID = sourceGUID,
        sourceName = sourceName,
        sourceFlags = sourceFlags,
        destGUID = destGUID,
        destName = destName,
        destFlags = destFlags,
      }
      PublishSnapshot("combat." .. kind, payload)
    end
  end
  if (subevent == "SPELL_SUMMON" or subevent == "SPELL_CREATE") and HasSubscribers("combat.owner.input") then
    Dispatch("combat.owner.input", sourceGUID, sourceName, sourceFlags, destGUID, destName, destFlags, tonumber(arg12) or 0, subevent)
    stats.ownerInputEvents = stats.ownerInputEvents + 1
  end
  if started > 0 then
    stats.decodeMs = stats.decodeMs
      + (debugprofilestop() - started) * PROFILE_SAMPLE_INTERVAL
    stats.profileSamples = stats.profileSamples + 1
  end
  if lightStarted > 0 then
    local elapsed = debugprofilestop() - lightStarted
    stats.lightProfileSamples = stats.lightProfileSamples + 1
    stats.lightProfileTotalMs = stats.lightProfileTotalMs + elapsed
    stats.lightProfileDownstreamMs =
      stats.lightProfileDownstreamMs + lightProfileDownstreamMs
    stats.lightProfileHubMs = stats.lightProfileHubMs
      + math.max(0, elapsed - lightProfileDownstreamMs)
  end
  lightProfileActive = false
  lightProfileDownstreamMs = 0
  ClearCurrentCombatLog()
  currentEventTime = nil
end

local function OnCombatLogEvent()
  if not CombatLogGetCurrentEventInfo then return end
  stats.nativeCombatReads = stats.nativeCombatReads + 1
  ProcessCombatLog(CombatLogGetCurrentEventInfo())
end

function RaidOptimizerHub.GetVersion()
  return VERSION
end

function RaidOptimizerHub.GetMode()
  return runtimeMode
end

local function RebuildDemandCategory(category)
  local totals = {}
  for _, categories in pairs(demandsByOwner) do
    local keys = categories[category]
    if keys then
      for key in pairs(keys) do totals[key] = (totals[key] or 0) + 1 end
    end
  end
  demandTotals[category] = totals
  if category == "wa.cleu" then
    local broad, spells, spellNames = {}, {}, {}
    for key in pairs(totals) do
      local subevent, spellName = tostring(key):match("^([^:]+):name:(.+)$")
      if subevent and spellName then
        local byName = spellNames[subevent]
        if not byName then byName = {}; spellNames[subevent] = byName end
        byName[spellName] = true
      else
        local spellId
        subevent, spellId = tostring(key):match("^([^:]+):(%d+)$")
        if subevent and spellId then
          local bySpell = spells[subevent]
          if not bySpell then bySpell = {}; spells[subevent] = bySpell end
          bySpell[tonumber(spellId)] = true
        else
          broad[key] = true
        end
      end
    end
    combatDemandBroad, combatDemandSpells, combatDemandSpellNames =
      broad, spells, spellNames
  end
end

function RaidOptimizerHub.SetDemandSnapshot(owner, category, keys)
  if type(owner) ~= "string" or owner == "" or type(category) ~= "string" or category == "" then
    return false
  end
  demandsByOwner[owner] = demandsByOwner[owner] or {}
  local snapshot = {}
  for key, enabled in pairs(keys or {}) do
    if enabled then snapshot[key] = true end
  end
  demandsByOwner[owner][category] = snapshot
  knownDemandCategories[category] = true
  RebuildDemandCategory(category)
  stats.demandUpdates = stats.demandUpdates + 1
  return true
end

function RaidOptimizerHub.ClearDemands(owner, category)
  local categories = demandsByOwner[owner]
  if not categories then return end
  if category then
    categories[category] = nil
    knownDemandCategories[category] = true
    RebuildDemandCategory(category)
  else
    local changed = {}
    for name in pairs(categories) do changed[name] = true end
    demandsByOwner[owner] = nil
    for name in pairs(changed) do RebuildDemandCategory(name) end
  end
end

function RaidOptimizerHub.HasDemand(category, key)
  if not knownDemandCategories[category] then return nil end
  return demandTotals[category] and demandTotals[category][key] ~= nil or false
end

function RaidOptimizerHub.HasCombatLogDemand(category, subevent, spellId, spellName)
  if not knownDemandCategories[category] then return nil end
  if category ~= "wa.cleu" then
    if spellId == nil then return RaidOptimizerHub.HasDemand(category, subevent) end
    return RaidOptimizerHub.HasDemand(category, tostring(subevent) .. ":" .. tostring(spellId))
  end
  if combatDemandBroad[subevent] then return true end
  local bySpell = combatDemandSpells[subevent]
  if bySpell and bySpell[spellId] then return true end
  local byName = combatDemandSpellNames[subevent]
  return byName and byName[spellName] and true or false
end

function RaidOptimizerHub.GetDemandSummary()
  local summary = {}
  for category, totals in pairs(demandTotals) do
    local keys, consumers = 0, 0
    for _, count in pairs(totals) do keys, consumers = keys + 1, consumers + count end
    summary[category] = { keys = keys, consumers = consumers }
  end
  return summary
end

function RaidOptimizerHub.IsCentralMode()
  return runtimeMode == "central"
end

function RaidOptimizerHub.SubscribeTick(owner, interval, callback)
  if runtimeMode ~= "central" then return false end
  if type(owner) ~= "string" or owner == "" or type(callback) ~= "function" then
    return false
  end
  tickSubscribers[owner] = {
    interval = math.max(1 / 60, tonumber(interval) or (1 / 30)),
    elapsed = 0,
    callback = callback,
  }
  local count = 0
  for _ in pairs(tickSubscribers) do count = count + 1 end
  stats.tickSubscribers = count
  return true
end

function RaidOptimizerHub.UnsubscribeTick(owner)
  tickSubscribers[owner] = nil
  local count = 0
  for _ in pairs(tickSubscribers) do count = count + 1 end
  stats.tickSubscribers = count
end

function RaidOptimizerHub.GetCapabilities()
  return {
    rawCombatForwarding = true,
    fullCombatLogInput = true,
    currentCombatLogCompatibility = true,
    combatLogFrameBinding = true,
    consumerTiming = true,
    damage = true,
    damageAggregateInput = true,
    ownershipInput = true,
    heal = true,
    pressure = true,
    callbackQuarantine = true,
    filteredFrameBinding = true,
    nativeCombatEventSemantics = true,
    compiledCombatDemandRouting = true,
    demandDrivenUnitData = RaidOptimizerHub.UnitDataProvider ~= nil,
    sharedUnitCache = RaidOptimizerHub.UnitDataProvider ~= nil,
    sharedAuraCache = RaidOptimizerHub.UnitDataProvider ~= nil,
    sharedCastCache = RaidOptimizerHub.UnitDataProvider ~= nil,
    frameProvider = RaidOptimizerHub.FrameProvider ~= nil,
    compiledCombatRoutes = true,
    compiledCombatDemands = true,
    fastAcquirePath = true,
    targetedWABatching = false,
    compiledWABatchPlans = false,
    lazyNativeAuraUpdates = false,
    waNativeAuraSignals = true,
    waExactDemandAuraScan = true,
    waCleuAuraVisibilityBarrier = true,
    waOrderedCleuDisplayPlan = true,
    waDeferredQueueNodeReuse = true,
    dynamicUnitEventRegistration = true,
    waAuraSnapshotBridge = false,
    exactWAMultiAuraFiltering = true,
    lowFpsLocationTrace = true,
    platerCombatLogSharing = true,
    platerCoreNativeSafe = true,
  }
end

function RaidOptimizerHub.Subscribe(owner, topic, callback, options)
  if type(owner) ~= "string" or owner == "" or type(topic) ~= "string" or type(callback) ~= "function" then
    return false, "invalid subscription"
  end
  subscriptions[topic] = subscriptions[topic] or {}
  subscriptions[topic][owner] = {
    callback = callback,
    options = options or {},
    errors = 0,
    windowStart = 0,
    disabled = false,
    calls = 0,
    workMs = 0,
    filterMs = 0,
    seen = 0,
    filtered = 0,
    delivered = 0,
    filterSamples = 0,
    workSamples = 0,
    lightWorkMs = 0,
    lightWorkSamples = 0,
  }
  if topic == "combat.cleu.input" then cleuRoutesDirty = true end
  ReconcileEvents()
  return true
end

local function SummarizeWeakAura(id)
  local data = type(WeakAuras) == "table"
    and type(WeakAuras.GetData) == "function"
    and WeakAuras.GetData(id) or nil
  local result = {
    id = tostring(id),
    parent = type(data) == "table" and data.parent or nil,
    regionType = type(data) == "table" and data.regionType or nil,
    triggerCount = 0,
    customTriggers = 0,
    everyFrameTriggers = 0,
    combatLogTriggers = 0,
  }
  local triggers = type(data) == "table" and data.triggers or nil
  if type(triggers) == "table" then
    for _, entry in pairs(triggers) do
      local trigger = type(entry) == "table" and entry.trigger or nil
      if type(trigger) == "table" then
        result.triggerCount = result.triggerCount + 1
        if trigger.type == "custom" then result.customTriggers = result.customTriggers + 1 end
        if trigger.check == "update" then
          result.everyFrameTriggers = result.everyFrameTriggers + 1
        end
        if trigger.event == "Combat Log"
          or tostring(trigger.events or ""):find(
            "COMBAT_LOG_EVENT_UNFILTERED", 1, true)
        then
          result.combatLogTriggers = result.combatLogTriggers + 1
        end
      end
    end
  end
  return result
end

function RaidOptimizerHub.RecordWeakAurasLoadChanges(changes, loaded)
  if runtimeMode ~= "central" or type(changes) ~= "table" then return false end
  local changed = 0
  for id, enabled in pairs(changes) do
    if enabled and type(id) == "string" then
      changed = changed + 1
      if loaded then
        waLoadedDisplays[id] = true
        waLoadedInventory[id] = SummarizeWeakAura(id)
      else
        waLoadedDisplays[id] = nil
        waLoadedInventory[id] = nil
      end
    end
  end
  if changed > 0 then
    if loaded then
      stats.waLoadBatches = stats.waLoadBatches + 1
    else
      stats.waUnloadBatches = stats.waUnloadBatches + 1
    end
    local count = 0
    for _ in pairs(waLoadedDisplays) do count = count + 1 end
    stats.waLoadedDisplayCount = count
  end
  return true
end

function RaidOptimizerHub.Unsubscribe(owner, topic)
  if subscriptions[topic] then subscriptions[topic][owner] = nil end
  if topic == "combat.cleu.input" then cleuRoutesDirty = true end
  ReconcileEvents()
end

function RaidOptimizerHub.UnsubscribeAll(owner)
  for _, topicSubscribers in pairs(subscriptions) do topicSubscribers[owner] = nil end
  cleuRoutesDirty = true
  boundFrames[owner] = nil
  RaidOptimizerHub.UnsubscribeTick(owner)
  ReconcileEvents()
end

function RaidOptimizerHub.RefreshCombatLogFrameRoutes(owner)
  if type(owner) ~= "string" or owner == "" then return false end
  local entry = subscriptions["combat.cleu.input"]
    and subscriptions["combat.cleu.input"][owner] or nil
  if not entry then return false end
  -- Dynamic consumers such as WeakAuras keep their already-owned subevent
  -- table and mutate it after a load/unload batch.  Mark only the routing
  -- index dirty; callback order, native trigger tables and event payloads are
  -- left untouched.
  cleuRoutesDirty = true
  return true
end

function RaidOptimizerHub.BindCombatLogFrame(owner, targetFrame, options)
  if type(owner) ~= "string" or owner == "" or not targetFrame or type(targetFrame.GetScript) ~= "function" then
    return false, "invalid frame binding"
  end
  if type(targetFrame.UnregisterEvent) == "function" then
    targetFrame:UnregisterEvent("COMBAT_LOG_EVENT_UNFILTERED")
  end
  if runtimeMode ~= "central" then
    boundFrames[owner] = nil
    RaidOptimizerHub.Unsubscribe(owner, "combat.cleu.input")
    if type(targetFrame.RegisterEvent) == "function" then
      targetFrame:RegisterEvent("COMBAT_LOG_EVENT_UNFILTERED")
      stats.nativeBypassBindings = stats.nativeBypassBindings + 1
      return true
    end
    return false, "native event registration unavailable"
  end
  boundFrames[owner] = targetFrame
  return RaidOptimizerHub.Subscribe(owner, "combat.cleu.input", function(...)
    local activeFrame = boundFrames[owner]
    if not activeFrame then return end
    local handler = activeFrame:GetScript("OnEvent")
    -- A tiny number of source-pinned private WA frames resolve
    -- CombatLogGetCurrentEventInfo through their custom environment instead
    -- of accepting payload arguments.  Their environment must see the Hub
    -- event only for this synchronous callback; leaving a replacement behind
    -- would change unrelated native events and was the old unsafe path.
    local releaseScope
    local enterScope = options and options.nativeEventScope
    if type(enterScope) == "function" then
      local scopeOk, releaseOrError = xpcall(enterScope, function(err) return err end)
      if not scopeOk then error(releaseOrError) end
      if type(releaseOrError) == "function" then releaseScope = releaseOrError end
    end
    local called, callbackError = true, nil
    if handler then
      called, callbackError = xpcall(function()
        -- Bound CLEU handlers intentionally receive no repacked payload;
        -- they read the current event through the native-compatible bridge.
        handler(activeFrame, "COMBAT_LOG_EVENT_UNFILTERED")
      end, function(err) return err end)
    end
    if releaseScope then
      local releaseOk, releaseError = xpcall(releaseScope, function(err) return err end)
      if not releaseOk then error(releaseError) end
    end
    if not called then error(callbackError) end
  end, {
    boundFrame = true,
    filter = options and options.filter or nil,
    filterName = options and options.filterName or nil,
    subevents = options and options.subevents or nil,
    nativeEventSemantics = true,
    combatDemandCategory = options and options.combatDemandCategory or nil,
  })
end

function RaidOptimizerHub.UnbindCombatLogFrame(owner)
  boundFrames[owner] = nil
  RaidOptimizerHub.Unsubscribe(owner, "combat.cleu.input")
end

function RaidOptimizerHub.GetSnapshot(topic)
  return CopyTable(snapshots[topic] or {})
end

function RaidOptimizerHub.GetWeakAurasFrameResolverStatus()
  local result = {
    build = WA_FRAME_RESOLVER_BUILD,
    major = WA_FRAME_RESOLVER_MAJOR,
    expectedMinor = WA_FRAME_RESOLVER_MINOR,
    ready = false,
    publicBinding = false,
    publicNameplateReady = false,
    bindings = {},
    missingBindings = {},
  }
  if not LibStub or type(LibStub.GetLibrary) ~= "function" then
    result.reason = "libstub-unavailable"
    return result
  end
  local resolver, minor = LibStub:GetLibrary(WA_FRAME_RESOLVER_MAJOR, true)
  result.minor = tonumber(minor) or 0
  if type(resolver) ~= "table" then
    result.reason = "resolver-not-loaded"
    return result
  end
  result.provider = resolver.RaidOptimizerProviderBuild
  result.versionMatch = result.minor == WA_FRAME_RESOLVER_MINOR
    and result.provider == WA_FRAME_RESOLVER_BUILD
  result.apiReady = type(resolver.GetUnitFrame) == "function"
    and type(resolver.GetUnitNameplate) == "function"
    and type(resolver.RegisterCallback) == "function"
    and type(resolver.StartProfile) == "function"
    and type(resolver.StopProfile) == "function"
    and type(resolver.GetProfileData) == "function"
    and type(resolver.RaidOptimizerRegisterBinding) == "function"
  local bindingSites = type(resolver.RaidOptimizerBindingSites) == "table"
    and resolver.RaidOptimizerBindingSites or {}
  result.bindingsReady = true
  for _, site in ipairs(WA_FRAME_RESOLVER_SITES) do
    local bound = bindingSites[site] == WA_FRAME_RESOLVER_BUILD
    result.bindings[site] = bound
    if not bound then
      result.bindingsReady = false
      result.missingBindings[#result.missingBindings + 1] = site
    end
  end
  result.publicBinding = type(WeakAuras) == "table"
    and WeakAuras.GetUnitFrame == resolver.GetUnitFrame
  result.publicNameplateReady = type(WeakAuras) == "table"
    and type(WeakAuras.GetUnitNameplate) == "function"
  result.ready = result.versionMatch and result.apiReady and result.bindingsReady
    and result.publicBinding and result.publicNameplateReady
  if not result.versionMatch then
    result.reason = "resolver-version-mismatch"
  elseif not result.apiReady then
    result.reason = "resolver-api-incomplete"
  elseif not result.bindingsReady then
    result.reason = "binding-sites-incomplete"
  elseif not result.publicBinding or not result.publicNameplateReady then
    result.reason = "public-binding-mismatch"
  end
  return result
end

function RaidOptimizerHub.HasSubscribers(topic)
  return HasSubscribers(topic) and true or false
end

function RaidOptimizerHub.PublishProvider(topic, snapshot, ...)
  if type(topic) ~= "string" or type(snapshot) ~= "table" then return false end
  if not HasSubscribers(topic) then
    stats.providerPublishSkips = stats.providerPublishSkips + 1
    return true
  end
  stats.providerPublishes = stats.providerPublishes + 1
  PublishSnapshot(topic, snapshot, ...)
  return true
end

function RaidOptimizerHub.GetStats()
  local result = CopyTable(stats)
  result.version = VERSION
  result.mode = runtimeMode
  result.demands = RaidOptimizerHub.GetDemandSummary()
  result.subscribers = {}
  result.consumers = {}
  result.boundFrames = 0
  result.subeventCounts = CopyTable(subeventCounts)
  result.averageFps = stats.frameTimeMs > 0 and (stats.frameSamples * 1000 / stats.frameTimeMs) or 0
  local function PercentileFrameMs(percentile)
    if stats.frameSamples <= 0 then return 0 end
    local target = math.max(1, math.ceil(stats.frameSamples * percentile))
    local seen = 0
    for bucket = 1, 200 do
      seen = seen + (frameTimeHistogram[bucket] or 0)
      if seen >= target then return bucket end
    end
    return 200
  end
  result.p99FrameMs = PercentileFrameMs(0.99)
  result.p999FrameMs = PercentileFrameMs(0.999)
  result.low1Fps = result.p99FrameMs > 0 and 1000 / result.p99FrameMs or 0
  result.low01Fps = result.p999FrameMs > 0 and 1000 / result.p999FrameMs or 0
  local function SessionPercentileFrameMs(percentile)
    if stats.sessionFrameSamples <= 0 then return 0 end
    local target = math.max(1, math.ceil(stats.sessionFrameSamples * percentile))
    local seen = 0
    for bucket = 1, 200 do
      seen = seen + (sessionFrameTimeHistogram[bucket] or 0)
      if seen >= target then return bucket end
    end
    return 200
  end
  result.sessionAverageFps = stats.sessionFrameTimeMs > 0
    and (stats.sessionFrameSamples * 1000 / stats.sessionFrameTimeMs) or 0
  result.sessionP99FrameMs = SessionPercentileFrameMs(0.99)
  result.sessionP999FrameMs = SessionPercentileFrameMs(0.999)
  result.sessionLow1Fps = result.sessionP99FrameMs > 0 and 1000 / result.sessionP99FrameMs or 0
  result.sessionLow01Fps = result.sessionP999FrameMs > 0 and 1000 / result.sessionP999FrameMs or 0
  result.currentZone = currentZoneName
  result.currentInstanceID = currentInstanceID
  result.currentIsZulGurub = currentIsZulGurub
  result.zoneFrames = zoneFrameStats
  result.locationSamples = locationSamples
  result.scriptProfile = type(GetCVar) == "function" and tostring(GetCVar("scriptProfile") or "0") or "unknown"
  for _ in pairs(boundFrames) do result.boundFrames = result.boundFrames + 1 end
  for topic, topicSubscribers in pairs(subscriptions) do
    local count = 0
    for owner, entry in pairs(topicSubscribers) do
      count = count + 1
      result.consumers[owner .. "@" .. topic] = {
        calls = entry.calls,
        workMs = entry.workMs,
        filterMs = entry.filterMs,
        seen = entry.seen,
        filtered = entry.filtered,
        delivered = entry.delivered,
        filterName = entry.options and entry.options.filterName or nil,
        errors = entry.errors,
        disabled = entry.disabled,
        lightWorkMs = entry.lightWorkMs or 0,
        lightWorkSamples = entry.lightWorkSamples or 0,
        boundFrame = entry.options and entry.options.boundFrame and true or false,
      }
    end
    result.subscribers[topic] = count
  end
  result.waLoadedDisplays = {}
  for id, inventory in pairs(waLoadedInventory) do
    result.waLoadedDisplays[id] = CopyTable(inventory)
  end
  if type(RaidOptimizerHub.GetFrameProviderStats) == "function" then
    result.frameProvider = RaidOptimizerHub.GetFrameProviderStats()
  end
  if type(RaidOptimizerHub.GetUnitDataStats) == "function" then
    result.unitData = RaidOptimizerHub.GetUnitDataStats()
  end
  local waAuraStats = _G.RaidOptimizerHubWeakAurasAuraSelectiveStats
  if type(waAuraStats) == "table" then
    result.waAuraSelective = {
      scans = tonumber(waAuraStats.scans) or 0,
      selectiveScans = tonumber(waAuraStats.selectiveScans) or 0,
      nativeFallbackScans = tonumber(waAuraStats.nativeFallbackScans) or 0,
      skippedUnitEvents = tonumber(waAuraStats.skippedUnitEvents) or 0,
      scannedAuras = tonumber(waAuraStats.scannedAuras) or 0,
      relevantAuras = tonumber(waAuraStats.relevantAuras) or 0,
      skippedAuras = tonumber(waAuraStats.skippedAuras) or 0,
      staleMatchesRemoved = tonumber(waAuraStats.staleMatchesRemoved) or 0,
      updateInfoChecks = tonumber(waAuraStats.updateInfoChecks) or 0,
      updateInfoSkips = tonumber(waAuraStats.updateInfoSkips) or 0,
      updateInfoFallbacks = tonumber(waAuraStats.updateInfoFallbacks) or 0,
      updateInfoAuraLookups = tonumber(waAuraStats.updateInfoAuraLookups) or 0,
      removedAuraCacheHits = tonumber(waAuraStats.removedAuraCacheHits) or 0,
      removedAuraCacheMisses = tonumber(waAuraStats.removedAuraCacheMisses) or 0,
      removedAuraSkips = tonumber(waAuraStats.removedAuraSkips) or 0,
    }
  end
  local waDispatchStats = _G.RaidOptimizerHubWeakAurasCleuDispatchStats
  if type(waDispatchStats) == "table" then
    result.waCleuDispatch = {
      events = tonumber(waDispatchStats.events) or 0,
      planBuilds = tonumber(waDispatchStats.planBuilds) or 0,
      spellPlanBuilds = tonumber(waDispatchStats.spellPlanBuilds) or 0,
      nativeDisplays = tonumber(waDispatchStats.nativeDisplays) or 0,
      plannedDisplays = tonumber(waDispatchStats.plannedDisplays) or 0,
      singleTriggerDispatches = tonumber(waDispatchStats.singleTriggerDispatches) or 0,
      multiTriggerFallbacks = tonumber(waDispatchStats.multiTriggerFallbacks) or 0,
      sourcePlayerRejects = tonumber(waDispatchStats.sourcePlayerRejects) or 0,
    }
  end
  local waQueueStats = _G.RaidOptimizerHubWeakAurasQueueStats
  if type(waQueueStats) == "table" then
    result.waQueue = {
      queued = tonumber(waQueueStats.queued) or 0,
      entryAllocations = tonumber(waQueueStats.entryAllocations) or 0,
      entryReuses = tonumber(waQueueStats.entryReuses) or 0,
      argsAllocations = tonumber(waQueueStats.argsAllocations) or 0,
      queueReuses = tonumber(waQueueStats.queueReuses) or 0,
      maxQueued = tonumber(waQueueStats.maxQueued) or 0,
    }
  end
  return result
end

-- Reused read-only snapshot for the raid flight recorder.  Unlike GetStats(),
-- this path does not copy route, subscriber, demand, or histogram tables each
-- second, so observing a raid does not create a new source of GC pressure.
local flightRecorderStats = {}
function RaidOptimizerHub.GetFlightRecorderStats()
  local consumerCalls, consumerWorkMs, consumerFilterMs = 0, 0, 0
  local waCalls, waWorkMs, waFilterMs = 0, 0, 0
  local nduiCalls, nduiWorkMs, nduiFilterMs, nduiConsumers = 0, 0, 0, 0
  local waConsumers = 0
  local detailsCalls, detailsWorkMs = 0, 0
  local boundFrameCount = 0
  for _ in pairs(boundFrames) do boundFrameCount = boundFrameCount + 1 end
  for _, topicSubscribers in pairs(subscriptions) do
    for owner, entry in pairs(topicSubscribers) do
      local calls = tonumber(entry.calls) or 0
      local workMs = tonumber(entry.workMs) or 0
      local filterMs = tonumber(entry.filterMs) or 0
      local ownerText = tostring(owner or "")
      if ownerText ~= "RaidOptimizerStressTest" and ownerText ~= "RaidOptimizerNativeBlackBox" then
        consumerCalls = consumerCalls + calls
        consumerWorkMs = consumerWorkMs + workMs
        consumerFilterMs = consumerFilterMs + filterMs
      end
      if ownerText:find("WeakAuras", 1, true) then
        waConsumers = waConsumers + 1
        waCalls = waCalls + calls
        waWorkMs = waWorkMs + workMs
        waFilterMs = waFilterMs + filterMs
      elseif ownerText:find("NDui", 1, true) then
        nduiConsumers = nduiConsumers + 1
        nduiCalls = nduiCalls + calls
        nduiWorkMs = nduiWorkMs + workMs
        nduiFilterMs = nduiFilterMs + filterMs
      elseif ownerText:lower():find("details", 1, true) then
        detailsCalls = detailsCalls + calls
        detailsWorkMs = detailsWorkMs + workMs
      end
    end
  end

  local unitEvents, unitRefreshes, auraRefreshes, fullAuraUpdates = 0, 0, 0, 0
  local incrementalAuraUpdates, modernAuraScans, legacyAuraScans = 0, 0, 0
  local unitPublishes, unitRefreshMs, unitFlushes, unitBudgetYields, unitDeferred = 0, 0, 0, 0, 0
  if type(RaidOptimizerHub.GetUnitDataFlightRecorderStats) == "function" then
    unitEvents, unitRefreshes, auraRefreshes, fullAuraUpdates,
      incrementalAuraUpdates, modernAuraScans, legacyAuraScans,
      unitPublishes, unitRefreshMs, unitFlushes, unitBudgetYields, unitDeferred =
      RaidOptimizerHub.GetUnitDataFlightRecorderStats()
  end

  local frameQueries, frameCacheHits, frameDirectHits, frameNameplateHits = 0, 0, 0, 0
  local frameMisses, frameScans, frameScanMs, frameCombatScanSkips = 0, 0, 0, 0
  if type(RaidOptimizerHub.GetFrameProviderFlightRecorderStats) == "function" then
    frameQueries, frameCacheHits, frameDirectHits, frameNameplateHits,
      frameMisses, frameScans, frameScanMs, frameCombatScanSkips =
      RaidOptimizerHub.GetFrameProviderFlightRecorderStats()
  end

  local result = flightRecorderStats
  result.received = stats.received or 0
  result.routeCandidates = stats.routeCandidates or 0
  result.routedFilterSkips = stats.routedFilterSkips or 0
  result.filtered = stats.filtered or 0
  result.dispatched = stats.dispatched or 0
  result.callbackErrors = stats.callbackErrors or 0
  result.quarantined = stats.quarantined or 0
  result.nativeEventPayloadSkips = stats.nativeEventPayloadSkips or 0
  result.compiledDemandChecks = stats.compiledDemandChecks or 0
  result.compiledDemandSkips = stats.compiledDemandSkips or 0
  result.emptyRouteReuses = stats.emptyRouteReuses or 0
  result.rate = stats.rate or 0
  result.peakRate = stats.peakRate or 0
  result.decodeMs = stats.decodeMs or 0
  result.luaMemoryKB = stats.luaMemoryKB or 0
  result.gcDrops = stats.gcDrops or 0
  result.gcDropKB = stats.gcDropKB or 0
  result.waBatchCalls = stats.waBatchCalls or 0
  result.waBatchGroups = stats.waBatchGroups or 0
  result.waBatchDisplays = stats.waBatchDisplays or 0
  result.waBatchRecoveries = stats.waBatchRecoveries or 0
  result.waBatchRecoveryErrors = stats.waBatchRecoveryErrors or 0
  result.waCleuVisibilityFastRejects = stats.waCleuVisibilityFastRejects or 0
  result.waCleuVisibilityDeferrals = stats.waCleuVisibilityDeferrals or 0
  result.waCleuVisibilityPayloadAllocations = stats.waCleuVisibilityPayloadAllocations or 0
  result.waCleuVisibilityPayloadReuses = stats.waCleuVisibilityPayloadReuses or 0
  result.waCleuVisibilityEntryAllocations = stats.waCleuVisibilityEntryAllocations or 0
  result.waCleuVisibilityEntryReuses = stats.waCleuVisibilityEntryReuses or 0
  local waDispatchStats = _G.RaidOptimizerHubWeakAurasCleuDispatchStats
  result.waCleuDispatchEvents = tonumber(waDispatchStats and waDispatchStats.events) or 0
  result.waCleuNativeDisplays = tonumber(waDispatchStats and waDispatchStats.nativeDisplays) or 0
  result.waCleuPlannedDisplays = tonumber(waDispatchStats and waDispatchStats.plannedDisplays) or 0
  result.waCleuSingleTriggerDispatches = tonumber(
    waDispatchStats and waDispatchStats.singleTriggerDispatches) or 0
  result.waCleuMultiTriggerFallbacks = tonumber(
    waDispatchStats and waDispatchStats.multiTriggerFallbacks) or 0
  local waQueueStats = _G.RaidOptimizerHubWeakAurasQueueStats
  result.waQueueEvents = tonumber(waQueueStats and waQueueStats.queued) or 0
  result.waQueueEntryAllocations = tonumber(waQueueStats and waQueueStats.entryAllocations) or 0
  result.waQueueEntryReuses = tonumber(waQueueStats and waQueueStats.entryReuses) or 0
  result.waQueueArgsAllocations = tonumber(waQueueStats and waQueueStats.argsAllocations) or 0
  result.waQueueReuses = tonumber(waQueueStats and waQueueStats.queueReuses) or 0
  result.consumerCalls = consumerCalls
  result.consumerWorkMs = consumerWorkMs
  result.consumerFilterMs = consumerFilterMs
  result.waConsumerCalls = waCalls
  result.waConsumerWorkMs = waWorkMs
  result.waConsumerFilterMs = waFilterMs
  result.waConsumerCount = waConsumers
  result.boundFrames = boundFrameCount
  result.nduiConsumerCalls = nduiCalls
  result.nduiConsumerWorkMs = nduiWorkMs
  result.nduiConsumerFilterMs = nduiFilterMs
  result.nduiConsumerCount = nduiConsumers
  result.lightProfileSamples = stats.lightProfileSamples or 0
  result.lightProfileTotalMs = stats.lightProfileTotalMs or 0
  result.lightProfileDownstreamMs = stats.lightProfileDownstreamMs or 0
  result.lightProfileHubMs = stats.lightProfileHubMs or 0
  result.waLoadedDisplayCount = stats.waLoadedDisplayCount or 0
  result.detailsConsumerCalls = detailsCalls
  result.detailsConsumerWorkMs = detailsWorkMs
  result.unitEvents = unitEvents
  result.unitRefreshes = unitRefreshes
  result.auraRefreshes = auraRefreshes
  result.fullAuraUpdates = fullAuraUpdates
  result.incrementalAuraUpdates = incrementalAuraUpdates
  result.modernAuraScans = modernAuraScans
  result.legacyAuraScans = legacyAuraScans
  result.unitPublishes = unitPublishes
  result.unitRefreshMs = unitRefreshMs
  result.unitFlushes = unitFlushes
  result.unitBudgetYields = unitBudgetYields
  result.unitDeferredRefreshes = unitDeferred
  result.frameQueries = frameQueries
  result.frameCacheHits = frameCacheHits
  result.frameDirectHits = frameDirectHits
  result.frameNameplateHits = frameNameplateHits
  result.frameMisses = frameMisses
  result.frameScans = frameScans
  result.frameScanMs = frameScanMs
  result.frameCombatScanSkips = frameCombatScanSkips
  return result
end

CollectAddonMetrics = function()
  local metrics = {}
  if type(UpdateAddOnCPUUsage) == "function" then pcall(UpdateAddOnCPUUsage) end
  if type(UpdateAddOnMemoryUsage) == "function" then pcall(UpdateAddOnMemoryUsage) end
  for _, addonName in ipairs(METRIC_ADDONS) do
    local loaded = false
    if C_AddOns and type(C_AddOns.IsAddOnLoaded) == "function" then
      loaded = C_AddOns.IsAddOnLoaded(addonName)
    elseif type(IsAddOnLoaded) == "function" then
      loaded = IsAddOnLoaded(addonName)
    end
    if loaded then
      local cpu, memory = 0, 0
      if type(GetAddOnCPUUsage) == "function" then
        local ok, value = pcall(GetAddOnCPUUsage, addonName)
        if ok then cpu = tonumber(value) or 0 end
      end
      if type(GetAddOnMemoryUsage) == "function" then
        local ok, value = pcall(GetAddOnMemoryUsage, addonName)
        if ok then memory = tonumber(value) or 0 end
      end
      metrics[addonName] = { cpuMs = cpu, memoryKB = memory }
    end
  end
  return metrics
end

local function SaveStatus()
  if not db then return end
  local report = RaidOptimizerHub.GetStats()
  report.waFrameResolver = RaidOptimizerHub.GetWeakAurasFrameResolverStatus()
  report.elapsed = math.max(0, time() - (stats.startedAt or time()))
  report.addonMetrics = CollectAddonMetrics()
  report.addonMetricDeltas = {}
  report.combatWindowAddonCpu = CopyTable(combatWindowAddonCpu)
  for addonName, current in pairs(report.addonMetrics) do
    local initial = sessionStartAddonMetrics and sessionStartAddonMetrics[addonName]
    if initial then
      report.addonMetricDeltas[addonName] = {
        cpuMs = math.max(0, (current.cpuMs or 0) - (initial.cpuMs or 0)),
        memoryKB = (current.memoryKB or 0) - (initial.memoryKB or 0),
      }
    end
  end
  db.latest = report
  db.latest.savedAt = time()
  db.abRuns = db.abRuns or {}
  db.abRuns[runtimeMode] = report
end

frame:RegisterEvent("ADDON_LOADED")
frame:RegisterEvent("PLAYER_ENTERING_WORLD")
frame:RegisterEvent("ZONE_CHANGED_NEW_AREA")
frame:RegisterEvent("PLAYER_REGEN_DISABLED")
frame:RegisterEvent("PLAYER_REGEN_ENABLED")
frame:RegisterEvent("PLAYER_LOGOUT")
frame:SetScript("OnEvent", function(_, event, name)
  if event == "ADDON_LOADED" and name == ADDON then
    db = RaidOptimizerHubDB
    db.version = VERSION
    -- Dynamic-group batching remains native. The only display-side guard
    -- prevents WA from entering an operation Blizzard already forbids:
    -- changing geometry of a genuinely protected frame during combat. The
    -- exact native PositionChildren transaction is replayed after combat.
    db.waEventBatch = false
    db.waProtectedLayoutTimer = false
    db.waProtectedCustomGrowLayout = true
    -- Third-party frames can hand their only CLEU registration to the hub.
    -- Keep the shared bus active even when an old test build left enabled=false.
    db.enabled = true
    runtimeMode = ResolveRuntimeMode()
    db.mode = runtimeMode
    ReconcileEvents()
    SaveStatus()
    return
  end
  if event == "COMBAT_LOG_EVENT_UNFILTERED" then
    if runtimeMode == "native" then
      stats.nativeObservedEvents = stats.nativeObservedEvents + 1
      TrackCombatWindowEvent()
    else
      OnCombatLogEvent()
    end
    return
  end
  if event == "UNIT_AURA" then
    VFInvalidateAuraUnit(name)
    return
  end
  if event == "PLAYER_TARGET_CHANGED" then
    VFInvalidateAuraUnit("target")
    VFRefreshAuraGUIDMap()
    return
  end
  if event == "PLAYER_FOCUS_CHANGED" then
    VFInvalidateAuraUnit("focus")
    VFRefreshAuraGUIDMap()
    return
  end
  if event == "PLAYER_ENTERING_WORLD" then
    UpdateZoneContext()
    if vfAuraInvalidationEnabled then
      VFInvalidateAuraUnit("player")
      VFInvalidateAuraUnit("target")
      VFInvalidateAuraUnit("focus")
      VFRefreshAuraGUIDMap()
    end
    if not sessionStartAddonMetrics then sessionStartAddonMetrics = CollectAddonMetrics() end
    SaveStatus()
    return
  end
  if event == "ZONE_CHANGED_NEW_AREA" then
    UpdateZoneContext()
    return
  end
  if event == "PLAYER_REGEN_DISABLED" then
    StartCombatWindow(GetTime and GetTime() or 0)
    return
  end
  if event == "PLAYER_REGEN_ENABLED" then
    FinishCombatWindow(GetTime and GetTime() or 0)
    return
  end
  if event == "PLAYER_LOGOUT" then
    FinishCombatWindow(GetTime and GetTime() or 0)
    SaveStatus()
  end
end)

frame:SetScript("OnUpdate", function(_, elapsed)
  if diagnosticsEnabled then
    SampleSessionFrame(elapsed)
    SampleLuaMemory(elapsed)
    SampleLocationWindow(elapsed)
  end
  if next(waBatchGuards) then RecoverInterruptedWABatches() end
  if runtimeMode == "central" then
    for _, entry in pairs(tickSubscribers) do
      entry.elapsed = entry.elapsed + (tonumber(elapsed) or 0)
      if entry.elapsed >= entry.interval then
        local deliveredElapsed = entry.elapsed
        entry.elapsed = entry.elapsed % entry.interval
        local ok = xpcall(entry.callback, geterrorhandler(), deliveredElapsed)
        if ok then
          stats.tickDispatches = stats.tickDispatches + 1
        else
          stats.tickErrors = stats.tickErrors + 1
        end
      end
    end
  end
  if diagnosticsEnabled and combatWindowActive and combatWindowLastEventAt
    and GetTime() - combatWindowLastEventAt < 0.5
  then
    local frameMs = math.max(0, tonumber(elapsed) or 0) * 1000
    if frameMs > 0 then
      stats.frameSamples = stats.frameSamples + 1
      stats.frameTimeMs = stats.frameTimeMs + frameMs
      stats.worstFrameMs = math.max(stats.worstFrameMs, frameMs)
      if frameMs >= 33.34 then stats.framesOver33Ms = stats.framesOver33Ms + 1 end
      if frameMs >= 50 then stats.framesOver50Ms = stats.framesOver50Ms + 1 end
      if frameMs >= 100 then stats.framesOver100Ms = stats.framesOver100Ms + 1 end
      local bucket = math.min(200, math.max(1, math.floor(frameMs) + 1))
      frameTimeHistogram[bucket] = (frameTimeHistogram[bucket] or 0) + 1
    end
  end
end)

SLASH_RAIDOPTIMIZERHUB1 = "/rohub"
SlashCmdList.RAIDOPTIMIZERHUB = function(message)
  local command = (message or ""):lower():match("^%s*(.-)%s*$")
  -- The Hub itself has no visual panel.  Forward the natural user-facing
  -- command to the installed black-box panel when available, instead of
  -- silently writing a chat-line report that is easily missed in combat.
  if command == "" and SlashCmdList and SlashCmdList.RAIDOPTIMIZERSTRESS then
    SlashCmdList.RAIDOPTIMIZERSTRESS("status")
    return
  end
  if command == "reset" then
    stats.received, stats.dispatched, stats.callbackErrors, stats.quarantined = 0, 0, 0, 0
    stats.nativeCombatReads, stats.cleuDispatches = 0, 0
    stats.nativeObservedEvents = 0
    stats.filterChecks, stats.filtered = 0, 0
    stats.routedFilterSkips, stats.routeCandidates, stats.emptyRouteReuses = 0, 0, 0
    stats.peakRate, bucketEvents, stats.decodeMs = 0, 0, 0
    stats.frameSamples, stats.frameTimeMs, stats.worstFrameMs = 0, 0, 0
    stats.framesOver33Ms, stats.framesOver50Ms, stats.framesOver100Ms = 0, 0, 0
    stats.sessionFrameSamples, stats.sessionFrameTimeMs, stats.sessionWorstFrameMs = 0, 0, 0
    stats.sessionFramesOver33Ms, stats.sessionFramesOver50Ms, stats.sessionFramesOver100Ms = 0, 0, 0
    stats.gcChecks, stats.gcDrops, stats.gcDropKB, stats.largestGcDropKB = 0, 0, 0, 0
    subeventCounts, frameTimeHistogram, sessionFrameTimeHistogram, zoneFrameStats = {}, {}, {}, {}
    locationSamples = {}
    locationSampleElapsed, locationSampleFrames, locationSampleWorstMs, locationHeartbeat = 0, 0, 0, 0
    stats.locationSamplesCaptured = 0
    UpdateZoneContext()
    for _, topicSubscribers in pairs(subscriptions) do
      for _, entry in pairs(topicSubscribers) do
        entry.calls, entry.workMs, entry.filterMs, entry.errors = 0, 0, 0, 0
        entry.seen, entry.filtered, entry.delivered = 0, 0, 0
      end
    end
    SaveStatus()
    Print("已清空本次中枢统计。")
    return
  end
  local report = RaidOptimizerHub.GetStats()
  Print("v" .. VERSION .. "，底层读取 " .. tostring(report.nativeCombatReads) .. "，事件 " .. tostring(report.received) .. "，峰值 " .. tostring(report.peakRate) .. "/秒。")
  Print("运行模式 " .. (report.mode == "native" and "原生基线" or "中枢优化") .. "，原生旁路绑定 " .. tostring(report.nativeBypassBindings) .. "。")
  Print("原生观测事件 " .. tostring(report.nativeObservedEvents) .. "，仅用于 A/B 负载对齐。")
  Print("绑定框体 " .. tostring(report.boundFrames) .. "，回调 " .. tostring(report.dispatched) .. "，错误 " .. tostring(report.callbackErrors) .. "，隔离 " .. tostring(report.quarantined) .. "。")
  Print("前置过滤 " .. tostring(report.filtered) .. "/" .. tostring(report.filterChecks) .. "，未进入插件计算。")
  if report.unitData then
    Print("单位缓存命中 " .. tostring(report.unitData.cacheHits) .. "/" .. tostring(report.unitData.queries)
      .. "，刷新 " .. tostring(report.unitData.refreshes) .. "，合并 " .. tostring(report.unitData.coalesced) .. "。")
  end
end
