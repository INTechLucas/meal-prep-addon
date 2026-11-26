--[[
Client HTTP de l'add-on « Liseuse » (Home Assistant), sur le réseau local.
Toutes les réponses sont du JSON déjà mis en forme par l'add-on.
]]

local http = require("socket.http")
local ltn12 = require("ltn12")
local socket = require("socket")
local socketutil = require("socketutil")
local json = require("json")
local logger = require("logger")

local Client = {}
Client.__index = Client

function Client.new(url, jeton)
    local o = setmetatable({}, Client)
    o.url = (url or ""):gsub("/+$", "")
    o.jeton = jeton or ""
    return o
end

local function encoder(v)
    return (tostring(v):gsub("[^%w%-%._~]", function(c)
        return string.format("%%%02X", string.byte(c))
    end))
end

-- Construit "chemin?a=1&b=2" à partir d'une table de paramètres
function Client.chemin(base, params)
    if not params then return base end
    local morceaux = {}
    for k, v in pairs(params) do
        if v ~= nil and v ~= "" then
            table.insert(morceaux, encoder(k) .. "=" .. encoder(v))
        end
    end
    table.sort(morceaux)
    if #morceaux == 0 then return base end
    return base .. "?" .. table.concat(morceaux, "&")
end

-- Requête brute. Renvoie (corps, nil) ou (nil, message d'erreur).
function Client:brut(methode, chemin, corps_table, delai)
    if self.url == "" then
        return nil, "Adresse de l'add-on non configurée (config.lua)"
    end
    local morceaux = {}
    local entetes = { ["X-Jeton"] = self.jeton, ["Accept"] = "application/json" }
    local source
    if corps_table then
        local donnees = json.encode(corps_table)
        source = ltn12.source.string(donnees)
        entetes["Content-Type"] = "application/json"
        entetes["Content-Length"] = tostring(#donnees)
    end
    delai = delai or 20
    socketutil:set_timeout(delai, delai + 10)
    local ok, code, _, statut = pcall(function()
        return socket.skip(1, http.request{
            url = self.url .. chemin,
            method = methode,
            headers = entetes,
            source = source,
            sink = ltn12.sink.table(morceaux),
        })
    end)
    socketutil:reset_timeout()
    if not ok then
        logger.warn("Maison: requête", chemin, "en échec :", code)
        return nil, "Erreur réseau"
    end
    local texte = table.concat(morceaux)
    if type(code) ~= "number" then
        logger.warn("Maison: requête", chemin, "sans réponse :", code, statut)
        return nil, "Add-on injoignable (" .. tostring(code) .. ")"
    end
    if code == 401 then
        return nil, "Jeton refusé par l'add-on"
    end
    if code ~= 200 then
        local ok_json, erreur = pcall(json.decode, texte)
        if ok_json and type(erreur) == "table" and type(erreur.erreur) == "string" then
            return nil, erreur.erreur
        end
        return nil, "Erreur " .. tostring(code)
    end
    return texte
end

-- Requête JSON. Renvoie (table, nil) ou (nil, message d'erreur).
function Client:json(methode, chemin, corps_table, delai)
    local texte, err = self:brut(methode, chemin, corps_table, delai)
    if not texte then return nil, err end
    local ok, donnees = pcall(json.decode, texte)
    if not ok or type(donnees) ~= "table" then
        return nil, "Réponse illisible"
    end
    return donnees
end

function Client:get(chemin, params, delai)
    return self:json("GET", Client.chemin(chemin, params), nil, delai)
end

function Client:post(chemin, corps, delai)
    return self:json("POST", chemin, corps or {}, delai)
end

return Client
