local hub = RaidOptimizerHub
if not hub then return end

local COMBAT_EVENT = "COMBAT_LOG_EVENT_UNFILTERED"
local OWNER_NAMEPLATES = "ElvUI:NameplatesInterrupt"
local OWNER_ANNOUNCE = "ElvUI:InterruptAnnounce"
local hookedInterruptToggle = false
local bindings = {}

local function IsInterrupt(_, subevent)
  return subevent == "SPELL_INTERRUPT" or subevent == "SPELL_PERIODIC_INTERRUPT"
end

local function GetModule(name)
  local engine = type(ElvUI) == "table" and ElvUI[1]
  if not engine or type(engine.GetModule) ~= "function" then return nil end
  local ok, module = pcall(engine.GetModule, engine, name, true)
  return ok and module or nil
end

local function HasNativeCombatEvent(module)
  -- Some ElvUI/AceEvent wrappers expose a registration query, but standard
  -- AceEvent does not. Unknown module/event implementations stay completely
  -- native rather than being guessed at and potentially silenced.
  if not module or type(module.IsEventRegistered) ~= "function" then return false end
  local ok, registered = pcall(module.IsEventRegistered, module, COMBAT_EVENT)
  return ok and registered and true or false
end

local function RestoreModule(owner)
  local binding = bindings[owner]
  if type(hub.UnbindCombatLogFrame) == "function" then
    pcall(hub.UnbindCombatLogFrame, owner)
  elseif type(hub.Unsubscribe) == "function" then
    pcall(hub.Unsubscribe, owner, "combat.cleu.input")
  end
  if not binding then return true end

  binding.active = false
  local restored = true
  if binding.wasRegistered then
    restored = pcall(binding.adapter.RegisterEvent, binding.adapter, COMBAT_EVENT)
  end
  if restored then bindings[owner] = nil end
  return restored
end

local function RestoreAll()
  RestoreModule(OWNER_NAMEPLATES)
  RestoreModule(OWNER_ANNOUNCE)
end

local function SubscribeModule(owner, module)
  if not module
    or type(module.COMBAT_LOG_EVENT_UNFILTERED) ~= "function"
    or type(module.RegisterEvent) ~= "function"
    or type(module.UnregisterEvent) ~= "function"
    or type(hub.BindCombatLogFrame) ~= "function"
  then
    RestoreModule(owner)
    return false
  end
  if type(hub.IsCentralMode) ~= "function" or not hub.IsCentralMode() then
    RestoreModule(owner)
    return false
  end

  local previous = bindings[owner]
  if previous and previous.module == module and previous.active then return true end
  if previous and not RestoreModule(owner) then return false end
  if not HasNativeCombatEvent(module) then
    -- The module itself chose not to listen for CLEU. Do not enable, disable,
    -- or otherwise change that native state on its behalf.
    return false
  end

  local binding = {
    module = module,
    wasRegistered = true,
    active = false,
  }
  local adapter = {}
  binding.adapter = adapter

  function adapter:GetScript(kind)
    if kind ~= "OnEvent" then return nil end
    return function(_, event)
      local activeModule = binding.module
      if activeModule and type(activeModule.COMBAT_LOG_EVENT_UNFILTERED) == "function" then
        return activeModule:COMBAT_LOG_EVENT_UNFILTERED(event)
      end
    end
  end

  function adapter:UnregisterEvent(event)
    if event == COMBAT_EVENT and binding.module then
      binding.module:UnregisterEvent(event)
    end
  end

  function adapter:RegisterEvent(event)
    if event == COMBAT_EVENT and binding.wasRegistered and binding.module then
      binding.module:RegisterEvent(event)
    end
    -- BindCombatLogFrame calls this on its circuit-breaker path. Marking the
    -- binding inactive makes any later attach perform a fresh native-state
    -- check instead of assuming that the hub still owns the event.
    binding.active = false
  end

  bindings[owner] = binding
  local called, bound = pcall(hub.BindCombatLogFrame, owner, adapter, {
    filterName = "ElvUI interrupt only",
    subevents = { SPELL_INTERRUPT = true, SPELL_PERIODIC_INTERRUPT = true },
    filter = IsInterrupt,
  })
  if not called or not bound then
    -- BindCombatLogFrame may already have removed the native event before a
    -- later validation/subscription failure. Always return it before failing.
    RestoreModule(owner)
    return false
  end
  binding.active = true
  return true
end

local function BindNameplates()
  local nameplates = GetModule("NamePlates")
  if not nameplates or not nameplates.Initialized or type(nameplates.PlateGUID) ~= "table" then
    RestoreModule(OWNER_NAMEPLATES)
    return
  end
  SubscribeModule(OWNER_NAMEPLATES, nameplates)
end

local function BindInterruptAnnounce()
  local misc = GetModule("Misc")
  if not misc then
    RestoreModule(OWNER_ANNOUNCE)
    return
  end
  local engine = type(ElvUI) == "table" and ElvUI[1]
  local announce = engine and engine.db and engine.db.general and engine.db.general.interruptAnnounce
  if announce and announce ~= "NONE" then
    SubscribeModule(OWNER_ANNOUNCE, misc)
  else
    RestoreModule(OWNER_ANNOUNCE)
  end

  if not hookedInterruptToggle and type(hooksecurefunc) == "function" and type(misc.ToggleInterrupt) == "function" then
    hookedInterruptToggle = true
    hooksecurefunc(misc, "ToggleInterrupt", function()
      if C_Timer and C_Timer.After then
        C_Timer.After(0, BindInterruptAnnounce)
      else
        BindInterruptAnnounce()
      end
    end)
  end
end

local function Attach()
  if type(hub.IsCentralMode) ~= "function" or not hub.IsCentralMode() then
    RestoreAll()
    return
  end
  BindNameplates()
  BindInterruptAnnounce()
end

local frame = CreateFrame("Frame")
frame:RegisterEvent("PLAYER_ENTERING_WORLD")
frame:SetScript("OnEvent", function()
  if C_Timer and C_Timer.After then
    C_Timer.After(3, Attach)
  else
    Attach()
  end
end)
