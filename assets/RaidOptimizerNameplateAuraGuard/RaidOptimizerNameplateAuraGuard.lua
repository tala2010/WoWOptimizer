local ADDON_NAME = ...

local DISPLAY_CVARS = {
  "nameplateEnemyNpcAuraDisplay",
  "nameplateEnemyPlayerAuraDisplay",
  "nameplateFriendlyPlayerAuraDisplay",
}
local DISPLAY_BITS = {1, 2, 3}
local FRIENDLY_DEBUFF_CVAR = "nameplateShowDebuffsOnFriendly"

local frame = CreateFrame("Frame")
local originalBits = {}
local originalFriendlyDebuffs
local applied = false

local function ReadBit(cvar, bit)
  if type(GetCVarBitfield) ~= "function" then return nil end
  local ok, value = pcall(GetCVarBitfield, cvar, bit)
  if ok then return value and true or false end
  return nil
end

local function WriteBit(cvar, bit, value)
  if type(SetCVarBitfield) ~= "function" then return false end
  local ok = pcall(SetCVarBitfield, cvar, bit, value)
  return ok
end

local function ReadCVar(cvar)
  local ok, value = pcall(GetCVar, cvar)
  if ok then return value end
end

local function WriteCVar(cvar, value)
  if value == nil then return false end
  local ok = pcall(SetCVar, cvar, value)
  return ok
end

local function ApplyGuard()
  if applied then return end
  local changed = 0
  for _, cvar in ipairs(DISPLAY_CVARS) do
    originalBits[cvar] = {}
    for _, bit in ipairs(DISPLAY_BITS) do
      local value = ReadBit(cvar, bit)
      originalBits[cvar][bit] = value
      if value ~= nil and WriteBit(cvar, bit, false) then
        changed = changed + 1
      end
    end
  end
  originalFriendlyDebuffs = ReadCVar(FRIENDLY_DEBUFF_CVAR)
  if originalFriendlyDebuffs ~= nil and WriteCVar(FRIENDLY_DEBUFF_CVAR, "0") then
    changed = changed + 1
  end
  applied = true
  RaidOptimizerNameplateAuraGuardDB.lastApplied = date("%Y-%m-%d %H:%M:%S")
  RaidOptimizerNameplateAuraGuardDB.changedSlots = changed
end

local function RestoreGuard()
  if not applied then return end
  for cvar, bits in pairs(originalBits) do
    for bit, value in pairs(bits) do
      if value ~= nil then WriteBit(cvar, bit, value) end
    end
  end
  WriteCVar(FRIENDLY_DEBUFF_CVAR, originalFriendlyDebuffs)
  applied = false
  RaidOptimizerNameplateAuraGuardDB.lastRestored = date("%Y-%m-%d %H:%M:%S")
end

frame:RegisterEvent("ADDON_LOADED")
frame:RegisterEvent("PLAYER_LOGIN")
frame:RegisterEvent("PLAYER_LOGOUT")
frame:SetScript("OnEvent", function(_, event, name)
  if event == "ADDON_LOADED" and name == ADDON_NAME then
    RaidOptimizerNameplateAuraGuardDB = RaidOptimizerNameplateAuraGuardDB or {}
  elseif event == "PLAYER_LOGIN" then
    C_Timer.After(0, ApplyGuard)
  elseif event == "PLAYER_LOGOUT" then
    RestoreGuard()
  end
end)

SLASH_RAIDOPTIMIZERNAMEPLATEAURAGUARD1 = "/roguard"
SlashCmdList.RAIDOPTIMIZERNAMEPLATEAURAGUARD = function()
  print(("|cffffd36bRO guard|r active=%s changed=%d"):format(
    tostring(applied), RaidOptimizerNameplateAuraGuardDB.changedSlots or 0))
end
