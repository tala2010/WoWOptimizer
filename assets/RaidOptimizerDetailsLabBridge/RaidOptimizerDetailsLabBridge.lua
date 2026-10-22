local ADDON = "RaidOptimizerDetailsLabBridge"
local VERSION = "0.6.3"
local SAFE_NATIVE_DETAILS = true

RaidOptimizerDetailsLabBridgeDB = RaidOptimizerDetailsLabBridgeDB or {}

local db = RaidOptimizerDetailsLabBridgeDB
local frame = CreateFrame("Frame")
local attached = false
local lastPublish = 0
local detailsListener
local snapshotTicker
local displayTicker
local CORE_OWNER = "RaidOptimizerDamageCoreLab"
local core = {active = false, actors = {}, ownersByGUID = {}, total = 0, events = 0, workMs = 0}
local statusFrame
local statusText
local parserWasRegistered = false
local parserDisabled = false
local detailsCombatRequested = false
local DISPLAY_REFRESH_SECONDS = 0.25

-- Customer runtime is intentionally silent. Diagnostics remain in SavedVariables
-- and can be collected by the desktop support bundle without chat/screen notices.
local function Print(_) end
local function Notify(_) end

local function StatusMessage()
  local mode = tostring(db.mode or "等待")
  local reason = tostring(db.reason or "")
  local message = "v" .. VERSION .. "  模式: " .. mode
    .. "  更新: " .. tostring(db.updates or 0)
    .. "  错误: " .. tostring(db.publishErrors or 0)
    .. "  战斗: " .. tostring(db.combatStarts or 0) .. "/" .. tostring(db.combatEnds or 0)
    .. "\n中枢伤害事件: " .. tostring(db.coreEvents or 0)
    .. "  写入伤害: " .. tostring(db.displayInjectedDamage or 0)
    .. "  Details解析: " .. (db.parserDisabled and "已停" or "原版")
  if reason ~= "" then message = message .. "\n" .. reason end
  if db.lastError and db.lastError ~= "" then message = message .. "\n" .. tostring(db.lastError) end
  return message
end

local function ShowStatus()
  return StatusMessage()
end

_G.RaidOptimizerDetailsLabBridgeShowStatus = ShowStatus

local function GetDetails()
  local details = _G.Details
  if type(details) == "table" and type(details.GetCurrentCombat) == "function" then
    return details
  end
  return nil
end

local function UsesBlizzardDamageMeter()
  if type(C_DamageMeter) == "table"
    and (type(C_DamageMeter.GetCombatSessionFromType) == "function"
      or type(C_DamageMeter.GetCombatSessionFromID) == "function"
      or type(C_DamageMeter.GetSessionDurationSeconds) == "function")
  then
    return true
  end
  local details = _G.Details
  if details and details.DM == C_DamageMeter and type(details.DM) == "table" then return true end
  local framework = _G.DetailsFramework
  if framework and type(framework.IsAddonApocalypseWow) == "function" then
    local ok, enabled = pcall(framework.IsAddonApocalypseWow, framework)
    if ok and enabled then return true end
  end
  return false
end

local function GetParserFrame()
  local details = _G.Details
  local bridge = details and details.RaidOptimizerBridge
  return (bridge and bridge.parserFrame) or (_G.Details222 and _G.Details222.parser_frame)
end

local function DisableDetailsParser()
  local parserFrame = GetParserFrame()
  if SAFE_NATIVE_DETAILS then
    -- Formal builds must never let stale SavedVariables, /run snippets, or an
    -- older lab controller turn the display-only experiment back on.  Restore
    -- the native registration here as a final invariant, even if this helper
    -- is reached through a legacy code path.
    if parserFrame and type(parserFrame.RegisterEvent) == "function" then
      parserFrame:RegisterEvent("COMBAT_LOG_EVENT_UNFILTERED")
    end
    parserWasRegistered = true
    parserDisabled = false
    db.parserDisabled = false
    db.displayOnly = false
    return false, "正式安全模式禁止停用 Details 原生解析"
  end
  if not parserFrame or type(parserFrame.UnregisterEvent) ~= "function" then
    return false, "Details 解析帧尚未就绪"
  end
  if not parserDisabled then
    parserWasRegistered = parserFrame:IsEventRegistered("COMBAT_LOG_EVENT_UNFILTERED") and true or false
  end
  parserFrame:UnregisterEvent("COMBAT_LOG_EVENT_UNFILTERED")
  parserDisabled = not parserFrame:IsEventRegistered("COMBAT_LOG_EVENT_UNFILTERED")
  db.parserDisabled = parserDisabled
  return parserDisabled, parserDisabled and nil or "无法停用 Details 战斗日志解析"
end

local function RestoreDetailsParser()
  local parserFrame = GetParserFrame()
  if parserFrame and type(parserFrame.RegisterEvent) == "function" and (parserWasRegistered or parserDisabled) then
    parserFrame:RegisterEvent("COMBAT_LOG_EVENT_UNFILTERED")
  end
  parserDisabled = false
  db.parserDisabled = false
end

local function GetCurrentDetailsCombat(details)
  if not details then return nil end
  local combat = type(details.GetCurrentCombat) == "function" and details:GetCurrentCombat() or nil
  if not combat and type(details.tabela_vigente) == "table" then combat = details.tabela_vigente end
  if combat and type(combat.GetContainer) == "function" then return combat end
  return nil
end

local function EnsureDetailsCombat()
  local details = GetDetails()
  local combat = GetCurrentDetailsCombat(details)
  if combat and details and details.in_combat then return combat end
  local detailsBridge = details and details.RaidOptimizerBridge
  if detailsCombatRequested or not detailsBridge or type(detailsBridge.startCombat) ~= "function" then return nil end
  detailsCombatRequested = true
  db.forcedCombatStarts = (db.forcedCombatStarts or 0) + 1
  local ok, startError = pcall(detailsBridge.startCombat)
  if not ok then
    db.forcedCombatStartErrors = (db.forcedCombatStartErrors or 0) + 1
    db.lastForcedCombatError = tostring(startError)
    return nil
  end
  return GetCurrentDetailsCombat(details)
end

local function PublishDetailsSnapshot()
  if not attached or not db or db.enabled == false then return end
  local now = GetTime()
  if now - lastPublish < 0.5 then return end
  lastPublish = now

  local details = GetDetails()
  if not details or not RaidOptimizerHub or type(RaidOptimizerHub.PublishProvider) ~= "function" then
    db.publishErrors = (db.publishErrors or 0) + 1
    db.updatedAt = time()
    return
  end

  local combat = details:GetCurrentCombat()
  local snapshot = {
    source = "Details",
    version = VERSION,
    updatedAt = time(),
    inCombat = UnitAffectingCombat("player") and true or false,
    combatTime = 0,
    playerDamage = 0,
  }
  if combat then
    if type(combat.GetCombatTime) == "function" then
      snapshot.combatTime = tonumber(combat:GetCombatTime()) or 0
    end
    local playerName = UnitName("player")
    if playerName and type(combat.GetActor) == "function" then
      local actor = combat:GetActor(DETAILS_ATTRIBUTE_DAMAGE or 1, playerName)
      if actor then snapshot.playerDamage = tonumber(actor.total) or 0 end
    end
  end

  if RaidOptimizerHub.PublishProvider("details.snapshot", snapshot) then
    if not db.displayOnly and db.mode ~= "native-safe" then db.mode = "compare" end
    db.updates = (db.updates or 0) + 1
  else
    db.publishErrors = (db.publishErrors or 0) + 1
  end
  db.updatedAt = time()
end

local function SafePublishDetailsSnapshot()
  local ok, err = pcall(PublishDetailsSnapshot)
  if not ok then
    db.publishErrors = (db.publishErrors or 0) + 1
    db.lastError = tostring(err)
    db.mode = "error"
    db.reason = "读取 Details 摘要失败"
    db.updatedAt = time()
  end
end

local function StopSnapshotTicker()
  if snapshotTicker then
    snapshotTicker:Cancel()
    snapshotTicker = nil
  end
end

local function StopDisplayTicker()
  if displayTicker then
    displayTicker:Cancel()
    displayTicker = nil
  end
end

local function StartSnapshotTicker()
  SafePublishDetailsSnapshot()
  if not snapshotTicker and C_Timer and C_Timer.NewTicker then
    snapshotTicker = C_Timer.NewTicker(1, SafePublishDetailsSnapshot)
  end
end

local function ResetDamageCore()
  core.active = true
  core.actors = {}
  core.total = 0
  core.events = 0
  core.workMs = 0
  core.missingCombatTicks = 0
end

local function CacheUnitOwner(petUnit, ownerUnit)
  local petGUID = UnitGUID(petUnit)
  local ownerName = UnitName(ownerUnit)
  if petGUID and ownerName then
    core.ownersByGUID[petGUID] = {
      name = ownerName,
      guid = UnitGUID(ownerUnit),
      flags = ownerUnit == "player" and 0x511 or nil,
    }
  end
end

local function RefreshUnitOwners()
  CacheUnitOwner("pet", "player")
  if IsInRaid() then
    for index = 1, 40 do CacheUnitOwner("raidpet" .. index, "raid" .. index) end
  else
    for index = 1, 4 do CacheUnitOwner("partypet" .. index, "party" .. index) end
  end
end

local function OnOwnerInput(ownerGUID, ownerName, ownerFlags, petGUID, petName, petFlags, summonSpellId)
  if petGUID and ownerName then
    core.ownersByGUID[petGUID] = {
      name = ownerName,
      guid = ownerGUID,
      flags = ownerFlags,
      petName = petName,
      petFlags = petFlags,
      summonSpellId = summonSpellId,
    }
  end
end

local function NormalizeName(name)
  return tostring(name or ""):match("^[^-]+") or tostring(name or "")
end

local function GetCoreActor(name, guid, flags)
  if not name then return nil end
  local actor = core.actors[name]
  if not actor then
    actor = {
      name = name,
      guid = guid,
      flags = flags,
      total = 0,
      petDamage = 0,
      events = 0,
      crits = 0,
      spells = {},
      targets = {},
      pets = {},
    }
    core.actors[name] = actor
  else
    actor.guid = actor.guid or guid
    actor.flags = actor.flags or flags
  end
  return actor
end

local function OnDamageInput(sourceGUID, sourceName, sourceFlags, destGUID, destName, destFlags, spellId, amount, critical)
  if not core.active or not sourceName then return end
  local started = debugprofilestop and debugprofilestop() or 0
  amount = tonumber(amount) or 0
  local actor = GetCoreActor(sourceName, sourceGUID, sourceFlags)
  actor.total = actor.total + amount
  actor.events = actor.events + 1
  if critical then actor.crits = actor.crits + 1 end

  local spellKey = spellId or 0
  local spell = actor.spells[spellKey]
  if not spell then
    spell = {
      total = 0,
      hits = 0,
      crits = 0,
      critTotal = 0,
      normalTotal = 0,
      critMin = 0,
      critMax = 0,
      normalMin = 0,
      normalMax = 0,
      targets = {},
    }
    actor.spells[spellKey] = spell
  end
  spell.total = spell.total + amount
  spell.hits = spell.hits + 1
  if critical then
    spell.crits = spell.crits + 1
    spell.critTotal = spell.critTotal + amount
    spell.critMin = spell.critMin == 0 and amount or math.min(spell.critMin, amount)
    spell.critMax = math.max(spell.critMax, amount)
  else
    spell.normalTotal = spell.normalTotal + amount
    spell.normalMin = spell.normalMin == 0 and amount or math.min(spell.normalMin, amount)
    spell.normalMax = math.max(spell.normalMax, amount)
  end

  local targetKey = destName or destGUID or "unknown"
  actor.targets[targetKey] = (actor.targets[targetKey] or 0) + amount
  spell.targets[targetKey] = (spell.targets[targetKey] or 0) + amount

  local ownerInfo = sourceGUID and core.ownersByGUID[sourceGUID]
  local ownerName = type(ownerInfo) == "table" and ownerInfo.name or ownerInfo
  ownerName = ownerName or sourceName:match("<([^<>]+)>%s*$")
  if ownerName and ownerName ~= "" then
    actor.ownerName = ownerName
    actor.ownerGUID = type(ownerInfo) == "table" and ownerInfo.guid or actor.ownerGUID
    actor.ownerFlags = type(ownerInfo) == "table" and ownerInfo.flags or actor.ownerFlags
    actor.summonSpellId = type(ownerInfo) == "table" and ownerInfo.summonSpellId or actor.summonSpellId
    local owner = GetCoreActor(ownerName, actor.ownerGUID, actor.ownerFlags)
    owner.petDamage = owner.petDamage + amount
    owner.pets[sourceName] = true
  end
  core.total = core.total + amount
  core.events = core.events + 1
  if started > 0 then core.workMs = core.workMs + (debugprofilestop() - started) end
end

local function FindCoreActor(playerName)
  if core.actors[playerName] then return core.actors[playerName] end
  local shortName = NormalizeName(playerName)
  for name, actor in pairs(core.actors) do
    if NormalizeName(name) == shortName then return actor end
  end
end

local function CountKeys(values)
  local count = 0
  for _ in pairs(values or {}) do count = count + 1 end
  return count
end

local function CopyTotals(source, target)
  for key in pairs(target) do target[key] = nil end
  for key, value in pairs(source or {}) do target[key] = value end
end

local function AddUnique(values, value)
  for index = 1, #values do
    if values[index] == value then return end
  end
  values[#values + 1] = value
end

local function SyncSpell(detailsSpell, spell)
  detailsSpell.total = spell.total
  detailsSpell.counter = spell.hits
  detailsSpell.c_amt = spell.crits
  detailsSpell.c_total = spell.critTotal
  detailsSpell.c_min = spell.critMin
  detailsSpell.c_max = spell.critMax
  detailsSpell.n_amt = spell.hits - spell.crits
  detailsSpell.n_total = spell.normalTotal
  detailsSpell.n_min = spell.normalMin
  detailsSpell.n_max = spell.normalMax
  CopyTotals(spell.targets, detailsSpell.targets)
end

local function RefreshDamageDisplayOrder(details, damageContainer)
  local actors = damageContainer and damageContainer._ActorTable
  if type(actors) ~= "table" then return end
  if type(damageContainer.SortByKey) == "function" then
    damageContainer:SortByKey("total")
  else
    table.sort(actors, function(a, b) return (a.total or 0) > (b.total or 0) end)
    if type(damageContainer.Remap) == "function" then damageContainer:Remap() end
  end
  damageContainer.need_refresh = true

  local groupCache = details and details.cache_damage_group
  if type(groupCache) == "table" then
    for index = #groupCache, 1, -1 do groupCache[index] = nil end
    for _, detailsActor in ipairs(actors) do
      if detailsActor.grupo and (tonumber(detailsActor.total) or 0) > 0 then
        groupCache[#groupCache + 1] = detailsActor
      end
    end
    table.sort(groupCache, function(a, b) return (a.total or 0) > (b.total or 0) end)
  end
end

local function SyncCoreToDetails(combatObject)
  if SAFE_NATIVE_DETAILS then
    -- Do not merely rely on the slash command guard.  The SavedVariables table
    -- is public and can be changed by an old WA or a /run command while logged
    -- in.  No ticker may write actors or totals in the formal native-safe mode.
    StopDisplayTicker()
    RestoreDetailsParser()
    db.displayOnly = false
    db.duplicateCoreDisabled = true
    db.mode = "native-safe"
    db.reason = "正确性保护：Details 使用原生解析，中枢仅观察摘要"
    core.active = false
    return true
  end
  if not db.displayOnly or not core.active then return true end
  local parserFrame = GetParserFrame()
  if parserFrame and parserFrame:IsEventRegistered("COMBAT_LOG_EVENT_UNFILTERED") then
    db.parserReassertions = (db.parserReassertions or 0) + 1
    local disabled, disableReason = DisableDetailsParser()
    if not disabled then return false, disableReason end
  end
  local details = GetDetails()
  local detailsBridge = details and details.RaidOptimizerBridge
  combatObject = combatObject or GetCurrentDetailsCombat(details)
  if not details then
    return false, "Details 主对象不可用"
  end
  if not combatObject or type(combatObject.GetContainer) ~= "function" then
    if core.events > 0 then combatObject = EnsureDetailsCombat() end
  end
  if not combatObject or type(combatObject.GetContainer) ~= "function" then
    core.missingCombatTicks = (core.missingCombatTicks or 0) + 1
    db.displayWaits = (db.displayWaits or 0) + 1
    if core.events > 0 and core.missingCombatTicks >= 20 then
      return false, "Details 当前战斗分段连续 5 秒不可用"
    end
    return true
  end
  core.missingCombatTicks = 0

  local damageContainer = combatObject:GetContainer(DETAILS_ATTRIBUTE_DAMAGE or 1)
  if not damageContainer or type(damageContainer.GetOrCreateActor) ~= "function" then
    return false, "Details 伤害容器不可用"
  end

  local combatTime = type(combatObject.GetCombatTime) == "function" and tonumber(combatObject:GetCombatTime()) or 0
  combatTime = math.max(combatTime or 0, 0.001)
  local now = GetTime()
  local combatStart = tonumber(combatObject.start_time)
  if not combatStart or combatStart <= 0 or combatStart > now then
    combatStart = now - combatTime
  end
  local combatEnd = tonumber(combatObject.end_time)
  if combatEnd and combatEnd <= combatStart then combatEnd = nil end
  local groupTotal = 0
  local injectedActors = 0

  for _, actor in pairs(core.actors) do
    local petContainer = detailsBridge and detailsBridge.petContainer
    if actor.ownerName and not actor.detailsPetRegistered and petContainer and type(petContainer.AddPet) == "function" then
      petContainer.AddPet(
        actor.guid,
        actor.name,
        actor.flags,
        actor.ownerGUID,
        actor.ownerName,
        actor.ownerFlags,
        actor.summonSpellId
      )
      actor.detailsPetRegistered = true
    end

    local detailsActor = damageContainer:GetOrCreateActor(actor.guid or ("Hub-" .. actor.name), actor.name, actor.flags or 0, true)
    if detailsActor then
      local displayTotal = actor.total + actor.petDamage
      detailsActor.total = displayTotal
      detailsActor.total_without_pet = actor.total
      detailsActor.last_dps = displayTotal / combatTime
      detailsActor.last_dps_realtime = detailsActor.last_dps
      detailsActor.dps_started = actor.events > 0
      detailsActor.last_event = now
      detailsActor.start_time = combatStart
      detailsActor.end_time = combatEnd
      detailsActor.delay = now
      detailsActor.on_hold = false
      detailsActor.need_refresh = true
      CopyTotals(actor.targets, detailsActor.targets)

      for spellId, spell in pairs(actor.spells) do
        local detailsSpell = detailsActor.spells:GetOrCreateSpell(spellId, true)
        if detailsSpell then SyncSpell(detailsSpell, spell) end
      end

      if not actor.ownerName then
        for petName in pairs(actor.pets) do AddUnique(detailsActor.pets, petName .. " <" .. actor.name .. ">") end
        if detailsActor.grupo then groupTotal = groupTotal + displayTotal end
      end
      injectedActors = injectedActors + 1
    end
  end

  combatObject.totals[DETAILS_ATTRIBUTE_DAMAGE or 1] = core.total
  combatObject.totals_grupo[DETAILS_ATTRIBUTE_DAMAGE or 1] = groupTotal
  RefreshDamageDisplayOrder(details, damageContainer)
  db.displaySyncs = (db.displaySyncs or 0) + 1
  db.displayInjectedActors = injectedActors
  db.displayInjectedDamage = core.total
  db.coreEvents = core.events
  db.coreTotalDamage = core.total
  db.coreWorkMs = core.workMs
  db.coreUsPerEvent = core.events > 0 and (core.workMs * 1000 / core.events) or 0
  local playerName = details.playername or UnitName("player")
  local playerActor = playerName and FindCoreActor(playerName)
  db.corePlayerDirectDamage = playerActor and playerActor.total or 0
  db.corePlayerPetDamage = playerActor and playerActor.petDamage or 0
  db.corePlayerDamage = db.corePlayerDirectDamage + db.corePlayerPetDamage
  db.detailsPlayerDamage = db.corePlayerDamage
  db.updatedAt = time()
  return true
end

local function FailDisplayOnly(reason)
  local fallbackReason = tostring(reason or "unknown")
  StopDisplayTicker()
  RestoreDetailsParser()
  db.displayOnly = false
  db.mode = "fallback-compare"
  db.reason = "只显示模式已回退: " .. fallbackReason
  db.lastFallback = fallbackReason
  db.lastFallbackAt = time()
  db.lastError = fallbackReason
  db.displayErrors = (db.displayErrors or 0) + 1
  db.updatedAt = time()
  Notify(db.reason)
end

local function SafeSyncCoreToDetails(combatObject)
  local ok, synced, reason = pcall(SyncCoreToDetails, combatObject)
  if not ok then
    FailDisplayOnly(synced)
    return false
  end
  if not synced then
    FailDisplayOnly(reason)
    return false
  end
  return true
end

local function StartDisplayTicker()
  StopDisplayTicker()
  if db.displayOnly and C_Timer and C_Timer.NewTicker then
    displayTicker = C_Timer.NewTicker(DISPLAY_REFRESH_SECONDS, SafeSyncCoreToDetails)
  end
end

local function EnterDisplayOnlyMode()
  if SAFE_NATIVE_DETAILS then
    RestoreDetailsParser()
    db.displayOnly = false
    return false, "正式安全模式禁止停用 Details 原生解析"
  end
  local disabled, reason = DisableDetailsParser()
  if not disabled then return false, reason end
  db.displayOnly = true
  db.mode = "display-only"
  db.reason = "中枢计算，Details 仅显示"
  db.damageDelta = "只显示模式"
  db.damageMatch = false
  db.updatedAt = time()
  return true
end

local function EnterCompareMode()
  StopDisplayTicker()
  RestoreDetailsParser()
  db.displayOnly = false
  db.mode = "compare"
  db.reason = "中枢与 Details 双算对账"
  db.updatedAt = time()
end

local function CompareDamageCore(combatObject)
  core.active = false
  local details = GetDetails()
  local playerName = details and (details.playername or UnitName("player")) or UnitName("player")
  local detailsActor = combatObject and playerName and combatObject:GetActor(DETAILS_ATTRIBUTE_DAMAGE or 1, playerName)
  local coreActor = playerName and FindCoreActor(playerName)
  local detailsDamage = detailsActor and tonumber(detailsActor.total) or 0
  local coreDirectDamage = coreActor and tonumber(coreActor.total) or 0
  local corePetDamage = coreActor and tonumber(coreActor.petDamage) or 0
  local coreDamage = coreDirectDamage + corePetDamage

  db.coreEvents = core.events
  db.coreTotalDamage = core.total
  db.corePlayerDamage = coreDamage
  db.corePlayerDirectDamage = coreDirectDamage
  db.corePlayerPetDamage = corePetDamage
  db.detailsPlayerDamage = detailsDamage
  db.damageDelta = coreDamage - detailsDamage
  db.damageMatch = math.abs(coreDamage - detailsDamage) < 0.1
  db.coreWorkMs = core.workMs
  db.coreUsPerEvent = core.events > 0 and (core.workMs * 1000 / core.events) or 0
  db.coreActors = CountKeys(core.actors)
  db.coreSpells = coreActor and CountKeys(coreActor.spells) or 0
  db.coreTargets = coreActor and CountKeys(coreActor.targets) or 0
  db.coreOwnerLinks = CountKeys(core.ownersByGUID)
  db.comparedAt = time()
end

local function Attach()
  if attached then return true end
  local details = GetDetails()
  if not details then
    db.mode = "waiting"
    db.reason = "Details 尚未完成初始化"
    db.updatedAt = time()
    return false
  end
  if not RaidOptimizerHub or type(RaidOptimizerHub.PublishProvider) ~= "function" then
    db.mode = "waiting"
    db.reason = "数据中枢尚未完成初始化"
    db.updatedAt = time()
    return false
  end
  if type(RaidOptimizerHub.GetMode) == "function" and RaidOptimizerHub.GetMode() == "native" then
    RestoreDetailsParser()
    db.displayOnly = false
    db.mode = "native-baseline"
    db.reason = "原生基线：Details 使用自身解析"
    db.updatedAt = time()
    attached = true
    return true
  end
  if UsesBlizzardDamageMeter() then
    StopDisplayTicker()
    StopSnapshotTicker()
    if type(RaidOptimizerHub.UnsubscribeAll) == "function" then
      RaidOptimizerHub.UnsubscribeAll(CORE_OWNER)
    end
    parserDisabled = false
    db.parserDisabled = false
    db.duplicateCoreDisabled = false
    db.displayOnly = false
    db.mode = "blizzard-native"
    db.reason = "Details 直接显示暴雪内置伤害统计；中枢重复伤害核心已停"
    db.damageDelta = "暴雪原生接口"
    db.damageMatch = true
    db.coreEvents = 0
    db.coreWorkMs = 0
    db.coreUsPerEvent = 0
    db.duplicateCoreDisabled = true
    db.updatedAt = time()
    attached = true
    return true
  end
  if SAFE_NATIVE_DETAILS then
    StopDisplayTicker()
    RestoreDetailsParser()
    if type(RaidOptimizerHub.UnsubscribeAll) == "function" then
      RaidOptimizerHub.UnsubscribeAll(CORE_OWNER)
    end
    db.parserDisabled = false
    db.displayOnly = false
    db.duplicateCoreDisabled = true
    db.mode = "native-safe"
    db.reason = "正确性保护：Details 使用原生解析，中枢仅观察摘要"
    db.damageDelta = "原生统计"
    db.damageMatch = true
    db.coreEvents = 0
    db.coreWorkMs = 0
    db.coreUsPerEvent = 0
    db.updatedAt = time()
    attached = true
    StartSnapshotTicker()
    return true
  end
  if type(RaidOptimizerHub.Subscribe) ~= "function" then
    db.mode = "waiting"
    db.reason = "中枢伤害接口尚未完成初始化"
    db.updatedAt = time()
    return false
  end
  if db.displayOnly and not GetParserFrame() then
    db.mode = "waiting"
    db.reason = "等待 Details 解析帧完成初始化"
    db.updatedAt = time()
    return false
  end
  local subscribed, subscribeError = RaidOptimizerHub.Subscribe(CORE_OWNER, "combat.damage.input", OnDamageInput)
  if not subscribed then
    db.mode = "error"
    db.reason = "中枢伤害订阅失败: " .. tostring(subscribeError or "unknown")
    db.updatedAt = time()
    return false
  end
  local ownerSubscribed, ownerSubscribeError = RaidOptimizerHub.Subscribe(CORE_OWNER, "combat.owner.input", OnOwnerInput)
  if not ownerSubscribed then
    RaidOptimizerHub.Unsubscribe(CORE_OWNER, "combat.damage.input")
    db.mode = "error"
    db.reason = "宠物归属订阅失败: " .. tostring(ownerSubscribeError or "unknown")
    db.updatedAt = time()
    return false
  end
  if type(details.CreateEventListener) ~= "function" then
    db.mode = "waiting"
    db.reason = "Details 事件接口尚未就绪"
    db.updatedAt = time()
    return false
  end

  detailsListener = details:CreateEventListener()
  detailsListener:RegisterEvent("COMBAT_PLAYER_ENTER", function()
    detailsCombatRequested = true
    db.combatStarts = (db.combatStarts or 0) + 1
    db.reason = ""
    ResetDamageCore()
    RefreshUnitOwners()
    if db.displayOnly then
      StartDisplayTicker()
    else
      StartSnapshotTicker()
    end
  end)
  detailsListener:RegisterEvent("COMBAT_PLAYER_LEAVE", function(_, combatObject)
    detailsCombatRequested = false
    db.combatEnds = (db.combatEnds or 0) + 1
    StopSnapshotTicker()
    StopDisplayTicker()
    if db.displayOnly then
      SafeSyncCoreToDetails(combatObject)
      core.active = false
      db.damageDelta = "只显示模式"
      db.damageMatch = true
    else
      SafePublishDetailsSnapshot()
      CompareDamageCore(combatObject)
    end
  end)
  attached = true
  if db.displayOnly then
    local displayReady, displayReason = EnterDisplayOnlyMode()
    if not displayReady then
      RaidOptimizerHub.Unsubscribe(CORE_OWNER, "combat.damage.input")
      RaidOptimizerHub.Unsubscribe(CORE_OWNER, "combat.owner.input")
      if detailsListener then
        detailsListener:UnregisterEvent("COMBAT_PLAYER_ENTER")
        detailsListener:UnregisterEvent("COMBAT_PLAYER_LEAVE")
        detailsListener = nil
      end
      db.mode = "waiting"
      db.reason = "等待只显示模式就绪: " .. tostring(displayReason)
      db.updatedAt = time()
      return false
    end
  else
    EnterCompareMode()
  end
  db.version = VERSION
  db.updatedAt = time()
  if not db.displayOnly then SafePublishDetailsSnapshot() end
  RefreshUnitOwners()
  if UnitAffectingCombat("player") then
    ResetDamageCore()
    if db.displayOnly then StartDisplayTicker() else StartSnapshotTicker() end
  end
  return true
end

local function TryAttach(attempt)
  if not db or db.enabled == false then return end
  local ok, result = pcall(Attach)
  if not ok then
    db.mode = "error"
    db.reason = "接入失败"
    db.lastError = tostring(result)
    db.publishErrors = (db.publishErrors or 0) + 1
    db.updatedAt = time()
    return
  end
  if result then
    Print("已接入 Details 会话数据。/tbzs 可查看。")
    return
  end
  if attempt < 12 and C_Timer and C_Timer.After then
    C_Timer.After(2, function() TryAttach(attempt + 1) end)
  end
end

frame:RegisterEvent("ADDON_LOADED")
frame:RegisterEvent("PLAYER_LOGIN")
frame:RegisterEvent("PLAYER_ENTERING_WORLD")
frame:RegisterEvent("PLAYER_LOGOUT")
frame:RegisterEvent("PLAYER_REGEN_DISABLED")
frame:SetScript("OnEvent", function(_, event, name)
  if event == "ADDON_LOADED" and name == ADDON then
    db = RaidOptimizerDetailsLabBridgeDB
    db.enabled = db.enabled ~= false
    db.displayOnly = false
    db.version = VERSION
    db.mode = db.enabled and "waiting" or "off"
    db.reason = db.enabled and "等待进入世界后初始化" or ""
    db.updates = 0
    db.publishErrors = 0
    db.combatStarts = 0
    db.combatEnds = 0
    db.coreEvents = 0
    db.coreTotalDamage = 0
    db.corePlayerDamage = 0
    db.corePlayerDirectDamage = 0
    db.corePlayerPetDamage = 0
    db.detailsPlayerDamage = 0
    db.damageDelta = "待测试"
    db.damageMatch = false
    db.coreWorkMs = 0
    db.coreUsPerEvent = 0
    db.coreActors = 0
    db.coreSpells = 0
    db.coreTargets = 0
    db.coreOwnerLinks = 0
    db.displaySyncs = 0
    db.displayErrors = 0
    db.displayInjectedActors = 0
    db.displayInjectedDamage = 0
    db.displayWaits = 0
    db.forcedCombatStarts = 0
    db.forcedCombatStartErrors = 0
    db.lastForcedCombatError = ""
    db.parserDisabled = false
    db.parserReassertions = 0
    db.duplicateCoreDisabled = false
    db.lastError = ""
    db.updatedAt = time()
    return
  end
  if event == "PLAYER_LOGIN" or event == "PLAYER_ENTERING_WORLD" then
    if C_Timer and C_Timer.After then
      C_Timer.After(1, function() TryAttach(1) end)
    else
      TryAttach(1)
    end
    return
  end
  if event == "PLAYER_REGEN_DISABLED" and db and db.displayOnly and attached then
    if SAFE_NATIVE_DETAILS then
      RestoreDetailsParser()
      db.displayOnly = false
      db.duplicateCoreDisabled = true
      db.mode = "native-safe"
      db.reason = "正确性保护：Details 使用原生解析，中枢仅观察摘要"
      core.active = false
      return
    end
    detailsCombatRequested = false
    EnsureDetailsCombat()
    if db.displayOnly and not core.active then
      ResetDamageCore()
      RefreshUnitOwners()
      StartDisplayTicker()
      db.combatStarts = (db.combatStarts or 0) + 1
    end
    return
  end
  if event == "PLAYER_LOGOUT" and db then
    StopSnapshotTicker()
    StopDisplayTicker()
    if RaidOptimizerHub and type(RaidOptimizerHub.Unsubscribe) == "function" then
      RaidOptimizerHub.Unsubscribe(CORE_OWNER, "combat.damage.input")
      RaidOptimizerHub.Unsubscribe(CORE_OWNER, "combat.owner.input")
    end
    db.updatedAt = time()
  end
end)

SLASH_RAIDOPTIMIZERDETAILSLAB1 = "/tbzs"
SlashCmdList.RAIDOPTIMIZERDETAILSLAB = function(message)
  local command = (message or ""):lower():match("^%s*(.-)%s*$")
  if command == "off" then
    db.enabled = false
    StopSnapshotTicker()
    StopDisplayTicker()
    RestoreDetailsParser()
    if RaidOptimizerHub and type(RaidOptimizerHub.Unsubscribe) == "function" then
      RaidOptimizerHub.Unsubscribe(CORE_OWNER, "combat.damage.input")
      RaidOptimizerHub.Unsubscribe(CORE_OWNER, "combat.owner.input")
    end
    if detailsListener then
      detailsListener:UnregisterEvent("COMBAT_PLAYER_ENTER")
      detailsListener:UnregisterEvent("COMBAT_PLAYER_LEAVE")
      detailsListener = nil
    end
    attached = false
    db.mode = "off"
    db.updatedAt = time()
    Notify("已停止读取 Details 会话数据；Details 本体没有被改动。")
    return
  end
  if command == "on" then
    db.enabled = true
    TryAttach(1)
    return
  end
  if command == "display" then
    if SAFE_NATIVE_DETAILS then
      Notify("正式安全模式不允许停止 Details 原生解析；排名、分段和宠物归属继续由 Details 处理。")
      return
    end
    if UsesBlizzardDamageMeter() then
      Notify("当前 Details 已使用暴雪内置伤害统计，无需启动重复伤害核心。")
      return
    end
    if UnitAffectingCombat("player") then
      Notify("请脱离战斗后再切换只显示模式。")
      return
    end
    StopSnapshotTicker()
    db.enabled = true
    local ok, reason = EnterDisplayOnlyMode()
    if not ok then
      Notify("只显示模式启动失败: " .. tostring(reason))
      return
    end
    if UnitAffectingCombat("player") then StartDisplayTicker() end
    Notify("已切换为中枢计算、Details 只显示。")
    return
  end
  if command == "compare" then
    if SAFE_NATIVE_DETAILS then
      Notify("正式安全模式已关闭重复伤害核心，当前保持 native-safe。")
      return
    end
    if UsesBlizzardDamageMeter() then
      Notify("当前 Details 已使用暴雪内置伤害统计，已保持单份计算。")
      return
    end
    if UnitAffectingCombat("player") then
      Notify("请脱离战斗后再切换双算模式。")
      return
    end
    EnterCompareMode()
    if UnitAffectingCombat("player") then StartSnapshotTicker() end
    Notify("已恢复 Details 原解析，进入双算对账模式。")
    return
  end
  if db.enabled and not attached then
    TryAttach(1)
  end
  ShowStatus()
  Notify("v" .. VERSION .. "，模式 " .. tostring(db.mode or "等待") .. "，更新 " .. tostring(db.updates or 0) .. "，错误 " .. tostring(db.publishErrors or 0) .. "。")
  Print("/tbzs 查看状态；/tbzs off 停止低频摘要读取。Details 原生解析始终保留。")
end
