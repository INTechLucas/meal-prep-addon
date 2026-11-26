--[[
Panneau plein écran : onglets Maison / Repas / Séances.
Le contenu de chaque onglet est une liste de blocs, répartie en pages (balayage gauche/droite).
]]

local Blitbuffer = require("ffi/blitbuffer")
local CenterContainer = require("ui/widget/container/centercontainer")
local Font = require("ui/font")
local FrameContainer = require("ui/widget/container/framecontainer")
local Geom = require("ui/geometry")
local GestureRange = require("ui/gesturerange")
local HorizontalGroup = require("ui/widget/horizontalgroup")
local HorizontalSpan = require("ui/widget/horizontalspan")
local InputContainer = require("ui/widget/container/inputcontainer")
local LineWidget = require("ui/widget/linewidget")
local Size = require("ui/size")
local TextBoxWidget = require("ui/widget/textboxwidget")
local TextWidget = require("ui/widget/textwidget")
local UIManager = require("ui/uimanager")
local VerticalGroup = require("ui/widget/verticalgroup")
local VerticalSpan = require("ui/widget/verticalspan")
local Device = require("device")
local Screen = Device.screen
local logger = require("logger")

local Carte = require("maison_carte")
local VueHtml = require("maison_vuehtml")

local ONGLETS = {
    { id = "maison", lib = "Maison" },
    { id = "repas", lib = "Repas" },
    { id = "seances", lib = "Séances" },
}
local RAFRAICHISSEMENT_AUTO = 300   -- secondes
local FLASH_TOUS_LES = 8            -- rafraîchissement complet (anti-rémanence) toutes les N mises à jour

local function texte(t)
    if type(t) == "string" then return t end
    if type(t) == "number" then return tostring(t) end
    return ""
end

local function liste(t)
    if type(t) == "table" then return t end
    return {}
end

local function joindre(morceaux, sep)
    local out = {}
    for _, m in ipairs(morceaux) do
        if type(m) == "string" and m ~= "" then table.insert(out, m) end
    end
    return table.concat(out, sep or " · ")
end

local Panneau = InputContainer:extend{
    client = nil,
    on_close = nil,
    covers_fullscreen = true,
}

function Panneau:init()
    self.W, self.H = Screen:getWidth(), Screen:getHeight()
    self.dimen = Geom:new{ x = 0, y = 0, w = self.W, h = self.H }
    self.marge = Screen:scaleBySize(10)
    self.ecart = Screen:scaleBySize(8)
    self.onglet = "maison"
    self.page = 1
    self.donnees = {}
    self.vues = { repas = "jour", seances = "jour" }
    self.jours = {}          -- date affichée par onglet (nil = aujourd'hui)
    self.statut = "Chargement…"
    self.nb_maj = 0
    self.ges_events = {
        Balayer = { GestureRange:new{ ges = "swipe", range = self.dimen } },
    }
    self.auto = function()
        self:charger(true)
        UIManager:scheduleIn(RAFRAICHISSEMENT_AUTO, self.auto)
    end
    self:construire()
end

-- --------------------------------------------------------------- cycle de vie

function Panneau:onShow()
    UIManager:setDirty(self, "full")
    UIManager:scheduleIn(RAFRAICHISSEMENT_AUTO, self.auto)
    UIManager:nextTick(function() self:charger() end)
    return true
end

function Panneau:onCloseWidget()
    UIManager:unschedule(self.auto)
end

function Panneau:fermer()
    UIManager:unschedule(self.auto)
    UIManager:close(self, "full")
    if self.on_close then self.on_close() end
end

function Panneau:onClose()
    self:fermer()
    return true
end

-- Au réveil de la liseuse : données fraîches
function Panneau:onResume()
    UIManager:scheduleIn(2, function() self:charger(true) end)
end

-- --------------------------------------------------------------- données

function Panneau:requete_onglet()
    local d = self.jours[self.onglet]
    if self.onglet == "maison" then
        return "/api/maison", nil
    elseif self.onglet == "repas" then
        return self.vues.repas == "semaine" and "/api/repas/semaine" or "/api/repas", { d = d }
    else
        return self.vues.seances == "semaine" and "/api/seances/semaine" or "/api/seances", { d = d }
    end
end

-- Affiche un message dans la barre d'état immédiatement (avant une requête bloquante)
function Panneau:annoncer(message)
    self.statut = message
    self:construire()
    UIManager:setDirty(self, "ui")
    UIManager:forceRePaint()
end

function Panneau:charger(silencieux)
    if not silencieux then self:annoncer("Chargement…") end
    local chemin, params = self:requete_onglet()
    local onglet = self.onglet
    local data, err = self.client:get(chemin, params, onglet == "seances" and 60 or 20)
    if onglet ~= self.onglet then return end
    if data then
        self.donnees[onglet] = data
        self.statut = "À jour à " .. os.date("%H:%M")
    else
        self.statut = "Erreur : " .. texte(err)
    end
    self:rafraichir()
end

function Panneau:action(message, chemin, corps)
    self:annoncer(message)
    local data, err = self.client:post(chemin, corps)
    if data then
        self.donnees.maison = data
        self.statut = "À jour à " .. os.date("%H:%M")
    else
        self.statut = "Erreur : " .. texte(err)
    end
    self:rafraichir()
end

function Panneau:rafraichir(mode)
    self:construire()
    self.nb_maj = self.nb_maj + 1
    if not mode then
        mode = (self.nb_maj % FLASH_TOUS_LES == 0) and "full" or "ui"
    end
    UIManager:setDirty(self, mode)
end

function Panneau:changer_onglet(id)
    if id == self.onglet then
        -- second appui : retour à aujourd'hui
        self.jours[id] = nil
    end
    self.onglet = id
    self.page = 1
    self:charger()
end

function Panneau:naviguer(d, vue)
    if vue then self.vues[self.onglet] = vue end
    self.jours[self.onglet] = d
    self.page = 1
    self:charger()
end

function Panneau:ouvrir_html(chemin, params)
    self:annoncer("Chargement…")
    local data, err = self.client:get(chemin, params, 60)
    if not data then
        self.statut = "Erreur : " .. texte(err)
        self:rafraichir()
        return
    end
    self.statut = "À jour à " .. os.date("%H:%M")
    self:construire()
    UIManager:show(VueHtml:new{
        titre = texte(data.titre),
        html = texte(data.html),
        css = texte(data.css),
        on_close = function() UIManager:setDirty(self, "full") end,
    })
end

function Panneau:onBalayer(_, ges)
    if not ges or not self.nb_pages or self.nb_pages < 2 then return true end
    if ges.direction == "west" then
        self:aller_page(self.page + 1)
    elseif ges.direction == "east" then
        self:aller_page(self.page - 1)
    end
    return true
end

function Panneau:aller_page(p)
    if p < 1 or p > (self.nb_pages or 1) then return end
    self.page = p
    self:rafraichir("ui")
end

-- --------------------------------------------------------------- briques d'affichage

function Panneau:largeur_utile()
    return self.W - 2 * self.marge
end

function Panneau:bouton(libelle, largeur, callback, opts)
    opts = opts or {}
    return Carte:new{
        lignes = { { texte = libelle, taille = opts.taille or 20, gras = opts.gras ~= false } },
        largeur = largeur,
        hauteur_min = opts.hauteur or Screen:scaleBySize(52),
        centre = true,
        inverse = opts.inverse,
        bordure = opts.bordure,
        callback = callback,
    }
end

function Panneau:titre_section(libelle)
    return VerticalGroup:new{
        align = "left",
        VerticalSpan:new{ width = Screen:scaleBySize(6) },
        TextWidget:new{
            text = libelle,
            face = Font:getFace("cfont", 21),
            bold = true,
            max_width = self:largeur_utile(),
        },
        VerticalSpan:new{ width = Screen:scaleBySize(2) },
        LineWidget:new{ dimen = Geom:new{ w = self:largeur_utile(), h = Size.line.thick } },
    }
end

function Panneau:paragraphe(t, taille, gras)
    return TextBoxWidget:new{
        text = t,
        face = Font:getFace("cfont", taille or 18),
        bold = gras,
        width = self:largeur_utile(),
    }
end

-- Rangée de widgets séparés par un écart horizontal
function Panneau:rangee(widgets)
    local g = HorizontalGroup:new{ align = "center" }
    for i, w in ipairs(widgets) do
        if i > 1 then table.insert(g, HorizontalSpan:new{ width = self.ecart }) end
        table.insert(g, w)
    end
    return g
end

-- Largeur de chaque case pour n cases sur une rangée
function Panneau:case(n)
    return math.floor((self:largeur_utile() - (n - 1) * self.ecart) / n)
end

-- Barre de navigation : ‹  titre  ›  [bascule jour/semaine]
function Panneau:barre_nav(titre, precedent, suivant, libelle_vue, vue_cible)
    local petit = Screen:scaleBySize(64)
    local vue_l = Screen:scaleBySize(120)
    local centre_l = self:largeur_utile() - 2 * petit - vue_l - 3 * self.ecart
    return self:rangee({
        self:bouton("‹", petit, function() self:naviguer(precedent) end, { taille = 26 }),
        Carte:new{
            lignes = { { texte = titre, taille = 19, gras = true } },
            largeur = centre_l,
            hauteur_min = Screen:scaleBySize(52),
            centre = true,
            bordure = 0,
        },
        self:bouton("›", petit, function() self:naviguer(suivant) end, { taille = 26 }),
        self:bouton(libelle_vue, vue_l, function() self:naviguer(self.jours[self.onglet], vue_cible) end,
            { taille = 18 }),
    })
end

-- --------------------------------------------------------------- onglet Maison

function Panneau:blocs_maison(d)
    local blocs = {}
    local infos = { texte(d.date) }
    if type(d.meteo) == "table" then
        table.insert(infos, joindre({ texte(d.meteo.lib), texte(d.meteo.temp) }, ", "))
    end
    local temps = {}
    for _, t in ipairs(liste(d.temperatures)) do
        table.insert(temps, texte(t.nom) .. " " .. texte(t.valeur))
    end
    table.insert(blocs, self:paragraphe(joindre(infos), 20, true))
    if #temps > 0 then
        table.insert(blocs, self:paragraphe(joindre(temps), 19))
    end

    local th = d.thermostat
    if type(th) == "table" then
        table.insert(blocs, self:titre_section(joindre({ texte(th.nom), texte(th.actuelle) }, " : ")
            .. (texte(th.action_lib) ~= "" and (" (" .. texte(th.action_lib) .. ")") or "")))
        local cote = Screen:scaleBySize(110)
        local milieu = self:largeur_utile() - 2 * cote - 2 * self.ecart
        table.insert(blocs, self:rangee({
            self:bouton("−", cote, function()
                self:action("Consigne…", "/api/thermostat", { delta = -0.5 })
            end, { taille = 30, hauteur = Screen:scaleBySize(64) }),
            Carte:new{
                lignes = {
                    { texte = "Consigne " .. texte(th.consigne_txt), taille = 24, gras = true },
                    { texte = texte(th.mode_lib), taille = 16 },
                },
                largeur = milieu,
                hauteur_min = Screen:scaleBySize(64),
                centre = true,
                bordure = Size.border.thin,
            },
            self:bouton("+", cote, function()
                self:action("Consigne…", "/api/thermostat", { delta = 0.5 })
            end, { taille = 30, hauteur = Screen:scaleBySize(64) }),
        }))
        local modes = liste(th.modes)
        if #modes > 0 then
            local boutons = {}
            for _, m in ipairs(modes) do
                local id = texte(m.id)
                table.insert(boutons, self:bouton(texte(m.lib), self:case(#modes), function()
                    if id ~= texte(th.mode) then
                        self:action("Mode…", "/api/thermostat", { mode = id })
                    end
                end, { inverse = id == texte(th.mode), taille = 18 }))
            end
            table.insert(blocs, self:rangee(boutons))
        end
    end

    for _, piece in ipairs(liste(d.pieces)) do
        table.insert(blocs, self:titre_section(texte(piece.nom)))
        local appareils = liste(piece.appareils)
        local par_rangee = 2
        for i = 1, #appareils, par_rangee do
            local cases = {}
            for j = i, math.min(i + par_rangee - 1, #appareils) do
                local a = appareils[j]
                local id = texte(a.id)
                table.insert(cases, Carte:new{
                    lignes = {
                        { texte = texte(a.nom), taille = 19, gras = true },
                        { texte = texte(a.etat_lib), taille = 16 },
                    },
                    largeur = self:case(par_rangee),
                    hauteur_min = Screen:scaleBySize(76),
                    inverse = a.allume == true,
                    callback = function()
                        self:action("Envoi…", "/api/basculer", { entity_id = id })
                    end,
                })
            end
            table.insert(blocs, self:rangee(cases))
        end
    end
    return blocs
end

-- --------------------------------------------------------------- onglet Repas

function Panneau:blocs_repas(d)
    local blocs = {}
    if self.vues.repas == "semaine" then
        table.insert(blocs, self:barre_nav(texte(d.titre), d.precedente, d.suivante, "Jour", "jour"))
        for _, j in ipairs(liste(d.jours)) do
            table.insert(blocs, self:titre_section(texte(j.titre) .. (j.aujourdhui == true and " · aujourd'hui" or "")))
            local repas = liste(j.repas)
            if #repas == 0 then
                table.insert(blocs, self:paragraphe("Rien de prévu.", 17))
            end
            for _, r in ipairs(repas) do
                local id, jour = r.id, texte(r.d)
                table.insert(blocs, Carte:new{
                    lignes = { { texte = joindre({ texte(r.moment), texte(r.titre),
                        r.option == true and "option" or "", texte(r.statut_lib) }), taille = 17 } },
                    largeur = self:largeur_utile(),
                    bordure = Size.border.thin,
                    marge_interne = Screen:scaleBySize(6),
                    callback = function() self:ouvrir_html("/api/recette", { repas = id, d = jour }) end,
                })
            end
        end
        return blocs
    end

    table.insert(blocs, self:barre_nav(texte(d.titre), d.precedent, d.suivant, "Semaine", "semaine"))
    local moments = liste(d.moments)
    if #moments == 0 then
        table.insert(blocs, self:paragraphe("Aucun repas prévu ce jour-là.", 19))
    end
    for _, m in ipairs(moments) do
        table.insert(blocs, self:titre_section(texte(m.lib)))
        for _, r in ipairs(liste(m.repas)) do
            local id, jour = r.id, texte(r.d)
            table.insert(blocs, Carte:new{
                lignes = {
                    { texte = texte(r.titre), taille = 20, gras = true },
                    { texte = joindre({ r.option == true and "Option" or "", texte(r.temps), texte(r.statut_lib) }), taille = 16 },
                    { texte = texte(r.portions), taille = 15 },
                    { texte = texte(r.note), taille = 15 },
                    { texte = texte(r.manque), taille = 15, gras = true },
                },
                largeur = self:largeur_utile(),
                bordure = r.option == true and Size.border.thin or Size.border.thick,
                callback = function() self:ouvrir_html("/api/recette", { repas = id, d = jour }) end,
            })
        end
    end
    return blocs
end

-- --------------------------------------------------------------- onglet Séances

function Panneau:blocs_seances(d)
    local blocs = {}
    if self.vues.seances == "semaine" then
        table.insert(blocs, self:barre_nav(texte(d.titre), d.precedente, d.suivante, "Jour", "jour"))
        for _, j in ipairs(liste(d.jours)) do
            table.insert(blocs, self:titre_section(texte(j.titre) .. (j.aujourdhui == true and " · aujourd'hui" or "")))
            local seances = liste(j.seances)
            if #seances == 0 then
                table.insert(blocs, self:paragraphe("Repos.", 17))
            end
            for _, s in ipairs(seances) do
                local a, id, jour = texte(s.athlete), texte(s.id), texte(s.d)
                table.insert(blocs, Carte:new{
                    lignes = { { texte = joindre({ texte(s.nom), texte(s.titre), texte(s.statut_lib) }), taille = 17 } },
                    largeur = self:largeur_utile(),
                    bordure = Size.border.thin,
                    marge_interne = Screen:scaleBySize(6),
                    inverse = s.statut == "realisee",
                    callback = function() self:ouvrir_html("/api/seance", { a = a, id = id, d = jour }) end,
                })
            end
        end
    else
        table.insert(blocs, self:barre_nav(texte(d.titre), d.precedent, d.suivant, "Semaine", "semaine"))
        for _, at in ipairs(liste(d.athletes)) do
            table.insert(blocs, self:titre_section(texte(at.nom)))
            local seances = liste(at.seances)
            if #seances == 0 then
                table.insert(blocs, self:paragraphe("Repos.", 18))
            end
            for _, s in ipairs(seances) do
                local a, id, jour = texte(at.id), texte(s.id), texte(s.d)
                table.insert(blocs, Carte:new{
                    lignes = {
                        { texte = texte(s.titre), taille = 20, gras = true },
                        { texte = joindre({ texte(s.sport), texte(s.intensite), texte(s.fc), texte(s.commune) }), taille = 16 },
                        { texte = texte(s.resume), taille = 15 },
                        { texte = joindre({ texte(s.statut_lib), texte(s.fait), texte(s.rpe) }), taille = 15, gras = true },
                    },
                    largeur = self:largeur_utile(),
                    callback = function() self:ouvrir_html("/api/seance", { a = a, id = id, d = jour }) end,
                })
            end
        end
    end
    for _, e in ipairs(liste(d.erreurs)) do
        table.insert(blocs, self:paragraphe("Indisponible : " .. texte(e), 15))
    end
    return blocs
end

-- --------------------------------------------------------------- assemblage

function Panneau:entete()
    local fermer = self:bouton("×", Screen:scaleBySize(56), function() self:fermer() end,
        { taille = 26, hauteur = Screen:scaleBySize(48) })
    local actualiser = self:bouton("Actualiser", Screen:scaleBySize(130), function() self:charger() end,
        { taille = 17, hauteur = Screen:scaleBySize(48) })
    local lib = "Maison"
    for _, o in ipairs(ONGLETS) do
        if o.id == self.onglet then lib = o.lib end
    end
    local reste = self:largeur_utile() - fermer:getSize().w - actualiser:getSize().w - 2 * self.ecart
    local gauche = VerticalGroup:new{
        align = "left",
        TextWidget:new{ text = lib, face = Font:getFace("cfont", 26), bold = true, max_width = reste },
        TextWidget:new{ text = self.statut or "", face = Font:getFace("cfont", 15), max_width = reste },
    }
    return VerticalGroup:new{
        align = "left",
        VerticalSpan:new{ width = self.marge },
        HorizontalGroup:new{
            align = "center",
            HorizontalSpan:new{ width = self.marge },
            CenterContainer:new{ dimen = Geom:new{ w = reste, h = gauche:getSize().h }, gauche },
            HorizontalSpan:new{ width = self.ecart },
            actualiser,
            HorizontalSpan:new{ width = self.ecart },
            fermer,
        },
        VerticalSpan:new{ width = self.ecart },
        LineWidget:new{ dimen = Geom:new{ w = self.W, h = Size.line.thick } },
    }
end

function Panneau:pied()
    local boutons = {}
    for _, o in ipairs(ONGLETS) do
        local id = o.id
        table.insert(boutons, self:bouton(o.lib, self:case(#ONGLETS), function() self:changer_onglet(id) end,
            { inverse = id == self.onglet, taille = 21, hauteur = Screen:scaleBySize(60) }))
    end
    return VerticalGroup:new{
        align = "center",
        LineWidget:new{ dimen = Geom:new{ w = self.W, h = Size.line.thick } },
        VerticalSpan:new{ width = self.ecart },
        self:rangee(boutons),
        VerticalSpan:new{ width = self.marge },
    }
end

function Panneau:pagination()
    local petit = Screen:scaleBySize(90)
    local milieu = self:largeur_utile() - 2 * petit - 2 * self.ecart
    return self:rangee({
        self:bouton("‹", petit, function() self:aller_page(self.page - 1) end,
            { taille = 24, hauteur = Screen:scaleBySize(46), bordure = Size.border.thin }),
        Carte:new{
            lignes = { { texte = string.format("Page %d / %d", self.page, self.nb_pages), taille = 17 } },
            largeur = milieu,
            hauteur_min = Screen:scaleBySize(46),
            centre = true,
            bordure = 0,
        },
        self:bouton("›", petit, function() self:aller_page(self.page + 1) end,
            { taille = 24, hauteur = Screen:scaleBySize(46), bordure = Size.border.thin }),
    })
end

-- Répartit les blocs en pages de hauteur h
function Panneau:paginer(blocs, h)
    local pages, courante, utilise = {}, {}, 0
    for _, b in ipairs(blocs) do
        local bh = b:getSize().h
        if #courante > 0 and utilise + self.ecart + bh > h then
            table.insert(pages, courante)
            courante, utilise = {}, 0
        end
        if #courante > 0 then utilise = utilise + self.ecart end
        table.insert(courante, b)
        utilise = utilise + bh
    end
    if #courante > 0 then table.insert(pages, courante) end
    if #pages == 0 then pages = { {} } end
    return pages
end

function Panneau:construire()
    local entete = self:entete()
    local pied = self:pied()
    local h_dispo = self.H - entete:getSize().h - pied:getSize().h - 2 * self.ecart

    local blocs
    local d = self.donnees[self.onglet]
    if type(d) ~= "table" then
        blocs = { self:paragraphe(self.statut or "", 20) }
    else
        local ok, res = pcall(function()
            if self.onglet == "maison" then return self:blocs_maison(d)
            elseif self.onglet == "repas" then return self:blocs_repas(d)
            else return self:blocs_seances(d) end
        end)
        if ok then
            blocs = res
        else
            logger.err("Maison: affichage impossible", res)
            blocs = { self:paragraphe("Affichage impossible : " .. tostring(res), 16) }
        end
    end

    local pages = self:paginer(blocs, h_dispo)
    local pagination
    if #pages > 1 then
        self.nb_pages = 1  -- provisoire, pour mesurer la barre
        local mesure = self:pagination():getSize().h + self.ecart
        pages = self:paginer(blocs, h_dispo - mesure)
    end
    self.nb_pages = #pages
    if self.page > self.nb_pages then self.page = self.nb_pages end
    if self.page < 1 then self.page = 1 end
    if self.nb_pages > 1 then
        pagination = self:pagination()
    end

    local corps = VerticalGroup:new{ align = "left" }
    for i, b in ipairs(pages[self.page]) do
        if i > 1 then table.insert(corps, VerticalSpan:new{ width = self.ecart }) end
        table.insert(corps, b)
    end
    local h_corps = h_dispo - (pagination and (pagination:getSize().h + self.ecart) or 0)

    local contenu = VerticalGroup:new{
        align = "left",
        entete,
        VerticalSpan:new{ width = self.ecart },
        HorizontalGroup:new{
            HorizontalSpan:new{ width = self.marge },
            VerticalGroup:new{
                align = "left",
                corps,
                VerticalSpan:new{ width = math.max(0, h_corps - corps:getSize().h) },
            },
        },
    }
    if pagination then
        table.insert(contenu, VerticalSpan:new{ width = self.ecart })
        table.insert(contenu, HorizontalGroup:new{ HorizontalSpan:new{ width = self.marge }, pagination })
    end
    table.insert(contenu, VerticalSpan:new{ width = self.ecart })
    table.insert(contenu, pied)

    self[1] = FrameContainer:new{
        background = Blitbuffer.COLOR_WHITE,
        bordersize = 0,
        padding = 0,
        margin = 0,
        width = self.W,
        height = self.H,
        contenu,
    }
end

return Panneau
