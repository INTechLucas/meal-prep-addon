--[[
Plugin KOReader « Panneau Maison » : transforme la liseuse en panneau de contrôle Home Assistant.
Les données viennent de l'add-on « Liseuse » (réseau local). La configuration (adresse, jeton) est dans
config.lua, à côté de ce fichier ; elle n'est jamais écrasée par les mises à jour automatiques.
]]

local InfoMessage = require("ui/widget/infomessage")
local NetworkMgr = require("ui/network/manager")
local UIManager = require("ui/uimanager")
local WidgetContainer = require("ui/widget/container/widgetcontainer")
local logger = require("logger")

local Client = require("maison_client")
local Panneau = require("maison_panneau")

-- État partagé par toutes les instances du plugin (une par écran KOReader) pendant la session
local session = {
    demarre = false,
    maj_verifiee = false,
    panneau = nil,
}

local function lire(chemin)
    local f = io.open(chemin, "rb")
    if not f then return nil end
    local t = f:read("*a")
    f:close()
    return t
end

local function ecrire(chemin, contenu)
    local f = io.open(chemin, "wb")
    if not f then return false end
    f:write(contenu)
    f:close()
    return true
end

local Maison = WidgetContainer:extend{
    name = "maison",
    is_doc_only = false,
}

function Maison:init()
    self.config = self:charger_config()
    self.ui.menu:registerToMainMenu(self)
    if not session.demarre and self.config.demarrage_auto ~= false then
        session.demarre = true
        UIManager:nextTick(function() self:ouvrir() end)
    end
end

function Maison:charger_config()
    local ok, cfg = pcall(dofile, self.path .. "/config.lua")
    if not ok or type(cfg) ~= "table" then
        logger.warn("Maison: config.lua absent ou invalide", cfg)
        cfg = {}
    end
    return cfg
end

function Maison:client()
    return Client.new(self.config.url, self.config.jeton)
end

function Maison:addToMainMenu(menu_items)
    menu_items.maison_panneau = {
        text = "Panneau Maison",
        sorting_hint = "tools",
        callback = function() self:ouvrir() end,
    }
end

function Maison:ouvrir()
    if session.panneau then return end
    local function afficher()
        session.panneau = Panneau:new{
            client = self:client(),
            garder_eveille = self.config.garder_eveille ~= false,
            on_close = function() session.panneau = nil end,
        }
        UIManager:show(session.panneau)
        if not session.maj_verifiee then
            session.maj_verifiee = true
            UIManager:scheduleIn(3, function() self:verifier_mise_a_jour() end)
        end
    end
    -- Réseau local uniquement : être connecté au Wi-Fi suffit (pas besoin d'accès Internet)
    if NetworkMgr:isConnected() then
        afficher()
    else
        NetworkMgr:runWhenConnected(afficher)
    end
end

--[[ Mise à jour automatique : l'add-on embarque la dernière version du plugin. Si elle diffère de la
version installée, les fichiers sont téléchargés puis KOReader redémarre. ]]
function Maison:verifier_mise_a_jour()
    local client = self:client()
    local manifeste = client:get("/plugin")
    if type(manifeste) ~= "table" or type(manifeste.version) ~= "string" then return end
    local installee = (lire(self.path .. "/version") or ""):gsub("%s+", "")
    if manifeste.version == installee then return end
    logger.info("Maison: mise à jour du plugin", installee, "->", manifeste.version)
    local fichiers = {}
    for _, f in ipairs(type(manifeste.fichiers) == "table" and manifeste.fichiers or {}) do
        if type(f.nom) ~= "string" or f.nom:find("%.%.") or f.nom:find("/") then return end
        local contenu = client:brut("GET", "/plugin/" .. f.nom)
        if not contenu or #contenu ~= f.taille then
            logger.warn("Maison: téléchargement incomplet", f.nom)
            return
        end
        fichiers[f.nom] = contenu
    end
    for nom, contenu in pairs(fichiers) do
        if not ecrire(self.path .. "/" .. nom .. ".nouveau", contenu) then return end
    end
    for nom in pairs(fichiers) do
        os.rename(self.path .. "/" .. nom .. ".nouveau", self.path .. "/" .. nom)
    end
    ecrire(self.path .. "/version", manifeste.version)
    UIManager:show(InfoMessage:new{ text = "Panneau Maison mis à jour.\nRedémarrage de KOReader…", timeout = 3 })
    UIManager:scheduleIn(3, function() UIManager:restartKOReader() end)
end

return Maison
