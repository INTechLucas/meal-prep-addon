--[[
Panneau plein écran : bandeau (heure, météo), onglets Maison / Repas / Séances.
Le contenu d'un onglet est une liste de blocs répartie en pages (balayage gauche/droite).
]]

local Blitbuffer = require("ffi/blitbuffer")
local FrameContainer = require("ui/widget/container/framecontainer")
local Geom = require("ui/geometry")
local GestureRange = require("ui/gesturerange")
local HorizontalGroup = require("ui/widget/horizontalgroup")
local HorizontalSpan = require("ui/widget/horizontalspan")
local InputContainer = require("ui/widget/container/inputcontainer")
local LineWidget = require("ui/widget/linewidget")
local Size = require("ui/size")
local UIManager = require("ui/uimanager")
local VerticalGroup = require("ui/widget/verticalgroup")
local VerticalSpan = require("ui/widget/verticalspan")
local Device = require("device")
local NetworkMgr = require("ui/network/manager")
local PluginShare = require("pluginshare")
local Screen = Device.screen
local logger = require("logger")

local UI = require("maison_carte")
local VueHtml = require("maison_vuehtml")
local Carte = UI.Carte
local icone, texte_w, paragraphe = UI.icone, UI.texte, UI.paragraphe

local ONGLETS = {
    { id = "maison", lib = "Maison", icone = "home" },
    { id = "repas", lib = "Repas", icone = "silverware_fork_knife" },
    { id = "seances", lib = "Séances", icone = "run" },
}
local RAFRAICHISSEMENT_AUTO = 300   -- secondes
local FLASH_TOUS_LES = 8            -- rafraîchissement complet (anti-rémanence) toutes les N mises à jour

local function S(x) return Screen:scaleBySize(x) end

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

local function rangee(widgets, ecart, align)
    local g = HorizontalGroup:new{ align = align or "center" }
    for i, w in ipairs(widgets) do
        if i > 1 and ecart and ecart > 0 then table.insert(g, HorizontalSpan:new{ width = ecart }) end
        table.insert(g, w)
    end
    return g
end

local function colonne(widgets, ecart, align)
    local g = VerticalGroup:new{ align = align or "left" }
    for i, w in ipairs(widgets) do
        if i > 1 and ecart and ecart > 0 then table.insert(g, VerticalSpan:new{ width = ecart }) end
        table.insert(g, w)
    end
    return g
end

local Panneau = InputContainer:extend{
    client = nil,
    on_close = nil,
    garder_eveille = true,
    covers_fullscreen = true,
}

function Panneau:init()
    self.W, self.H = Screen:getWidth(), Screen:getHeight()
    self.dimen = Geom:new{ x = 0, y = 0, w = self.W, h = self.H }
    self.marge = S(12)
    self.ecart = S(10)
    self.onglet = "maison"
    self.page = 1
    self.donnees = {}
    self.vues = { repas = "jour", seances = "jour" }
    self.jours = {}
    self.statut = "Chargement…"
    self.erreur = false
    self.nb_maj = 0
    self.ges_events = {
        Balayer = { GestureRange:new{ ges = "swipe", range = self.dimen } },
    }
    self.auto = function()
        self:charger(true)
        UIManager:scheduleIn(RAFRAICHISSEMENT_AUTO, self.auto)
    end
    self.horloge = function()
        self:construire()
        local h = self.h_entete or S(90)
        UIManager:setDirty(self, function() return "ui", Geom:new{ x = 0, y = 0, w = self.W, h = h } end)
        UIManager:scheduleIn(61 - tonumber(os.date("%S")), self.horloge)
    end
    self:construire()
end

-- --------------------------------------------------------------- cycle de vie

function Panneau:veille(empecher)
    if self.garder_eveille then
        PluginShare.pause_auto_suspend = empecher
    end
end

function Panneau:onShow()
    self:veille(true)
    UIManager:setDirty(self, "full")
    UIManager:scheduleIn(RAFRAICHISSEMENT_AUTO, self.auto)
    UIManager:scheduleIn(61 - tonumber(os.date("%S")), self.horloge)
    UIManager:nextTick(function() self:charger() end)
    return true
end

function Panneau:arreter()
    UIManager:unschedule(self.auto)
    UIManager:unschedule(self.horloge)
    self:veille(false)
end

function Panneau:onCloseWidget()
    self:arreter()
end

function Panneau:fermer()
    self:arreter()
    UIManager:close(self, "full")
    if self.on_close then self.on_close() end
end

function Panneau:onClose()
    self:fermer()
    return true
end

function Panneau:onResume()
    UIManager:scheduleIn(2, function()
        NetworkMgr:runWhenConnected(function() self:charger(true) end)
    end)
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

function Panneau:annoncer(message)
    self.statut, self.erreur = message, false
    self:construire()
    local h = self.h_entete or S(90)
    UIManager:setDirty(self, function() return "ui", Geom:new{ x = 0, y = 0, w = self.W, h = h } end)
    UIManager:forceRePaint()
end

function Panneau:resultat(data, err, cle)
    if data then
        self.donnees[cle] = data
        self.statut, self.erreur = nil, false
        -- la météo vient de l'onglet Maison : on la garde pour le bandeau
        if cle == "maison" then self.meteo = data.meteo end
    else
        self.statut, self.erreur = texte(err), true
    end
end

function Panneau:charger(silencieux)
    if not silencieux then self:annoncer("Chargement…") end
    local chemin, params = self:requete_onglet()
    local onglet = self.onglet
    local data, err = self.client:get(chemin, params, onglet == "seances" and 60 or 20)
    if onglet ~= self.onglet then return end
    self:resultat(data, err, onglet)
    if onglet ~= "maison" and not self.meteo then
        -- première ouverture sur un autre onglet : météo du bandeau
        local m = self.client:get("/api/maison")
        if m then self.donnees.maison, self.meteo = m, m.meteo end
    end
    self:rafraichir()
end

function Panneau:action(message, chemin, corps)
    self:annoncer(message)
    local data, err = self.client:post(chemin, corps)
    self:resultat(data, err, "maison")
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
    if id == self.onglet then self.jours[id] = nil end
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
        self.statut, self.erreur = texte(err), true
        self:rafraichir()
        return
    end
    self.statut, self.erreur = nil, false
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

-- --------------------------------------------------------------- briques

function Panneau:largeur_utile()
    return self.W - 2 * self.marge
end

function Panneau:case(n, largeur)
    largeur = largeur or self:largeur_utile()
    return math.floor((largeur - (n - 1) * self.ecart) / n)
end

-- Bouton carré/rond avec une icône (et éventuellement un libellé dessous)
function Panneau:bouton_icone(nom, cote, callback, opts)
    opts = opts or {}
    local contenu = icone(nom, opts.taille_icone or math.floor(cote * 0.5), opts.inverse)
    if opts.libelle then
        contenu = colonne({ contenu, texte_w(opts.libelle, opts.taille_libelle or 15,
            { gras = opts.inverse, blanc = opts.inverse }) }, S(2), "center")
    end
    return Carte:new{
        contenu = contenu,
        largeur = opts.largeur or cote,
        hauteur_min = opts.hauteur or cote,
        centre = true,
        inverse = opts.inverse,
        bordure = opts.bordure,
        rayon = opts.rayon,
        marge_interne = opts.marge or S(4),
        callback = callback,
    }
end

-- Puce : icône + texte sur une ligne
function Panneau:puce(nom, t, taille, opts)
    opts = opts or {}
    return rangee({
        icone(nom, opts.taille_icone or math.floor(taille * 1.3), opts.blanc),
        texte_w(t, taille, { gras = opts.gras, blanc = opts.blanc, largeur = opts.largeur }),
    }, S(6))
end

-- Titre de section : icône + libellé en gras, filet fin dessous
function Panneau:titre_section(libelle, nom_icone, droite)
    local tete = nom_icone and self:puce(nom_icone, libelle, 18, { gras = true }) or
        texte_w(libelle, 18, { gras = true, largeur = self:largeur_utile() })
    if droite then
        local reste = self:largeur_utile() - tete:getSize().w - droite:getSize().w
        tete = rangee({ tete, HorizontalSpan:new{ width = math.max(0, reste) }, droite })
    end
    local titre = colonne({
        VerticalSpan:new{ width = S(4) },
        tete,
        VerticalSpan:new{ width = S(4) },
        LineWidget:new{ dimen = Geom:new{ w = self:largeur_utile(), h = Size.line.thin } },
    })
    titre.est_titre = true
    return titre
end

-- Petite étiquette inversée (« aujourd'hui », « option »…)
function Panneau:etiquette(t)
    return Carte:new{
        contenu = texte_w(t, 13, { gras = true, blanc = true }),
        largeur = texte_w(t, 13, { gras = true }):getSize().w + 2 * S(8),
        centre = true,
        inverse = true,
        bordure = 0,
        rayon = S(8),
        marge_interne = S(3),
    }
end

-- Carte de liste : icône à gauche, lignes de texte, chevron (ou coche) à droite
function Panneau:ligne_liste(opts)
    local L = self:largeur_utile()
    local bordure = opts.bordure or Size.border.thick
    local marge = opts.marge or S(10)
    local interieur = Carte.interieur(L, bordure, marge)
    local g_icone = opts.icone and S(opts.taille_icone or 34) or 0
    local d_icone = S(22)
    local largeur_texte = interieur - d_icone - S(8) - (g_icone > 0 and (g_icone + S(10)) or 0)
    local lignes = {}
    for _, l in ipairs(opts.lignes) do
        if l.texte and l.texte ~= "" then
            table.insert(lignes, paragraphe(l.texte, l.taille or 16, largeur_texte, { gras = l.gras }))
        end
    end
    local elements = {}
    if g_icone > 0 then
        table.insert(elements, icone(opts.icone, g_icone))
        table.insert(elements, HorizontalSpan:new{ width = S(10) })
    end
    table.insert(elements, colonne(lignes, S(2)))
    table.insert(elements, HorizontalSpan:new{ width = S(8) })
    table.insert(elements, icone(opts.fait and "check_circle" or "chevron_right", d_icone))
    return Carte:new{
        contenu = rangee(elements, 0),
        largeur = L,
        bordure = bordure,
        marge_interne = marge,
        rayon = S(10),
        callback = opts.callback,
    }
end

-- Barre jour/semaine : ‹  titre  ›  [semaine]
function Panneau:barre_nav(titre, sous_titre, precedent, suivant, semaine_active)
    local cote = S(52)
    local centre_l = self:largeur_utile() - 3 * cote - 3 * self.ecart
    local centre = colonne({
        texte_w(titre, 20, { gras = true, largeur = centre_l }),
        sous_titre and self:etiquette(sous_titre) or nil,
    }, S(2), "center")
    return rangee({
        self:bouton_icone("chevron_left", cote, function() self:naviguer(precedent) end, { bordure = Size.border.thin }),
        Carte:new{ contenu = centre, largeur = centre_l, hauteur_min = cote, centre = true, bordure = 0,
                   marge_interne = 0 },
        self:bouton_icone("chevron_right", cote, function() self:naviguer(suivant) end, { bordure = Size.border.thin }),
        self:bouton_icone("calendar_week", cote, function()
            self:naviguer(self.jours[self.onglet], semaine_active and "jour" or "semaine")
        end, { inverse = semaine_active, bordure = Size.border.thin }),
    }, self.ecart)
end

function Panneau:vide(t)
    return paragraphe(t, 16, self:largeur_utile())
end

-- --------------------------------------------------------------- onglet Maison

function Panneau:bloc_thermostat(th)
    local L = self:largeur_utile()
    local bordure, marge = Size.border.thick, S(12)
    local interieur = Carte.interieur(L, bordure, marge)
    local mode = texte(th.mode)

    -- gauche : température de la pièce
    local etat = joindre({ texte(th.piece) ~= "" and texte(th.piece) or "Thermostat", texte(th.action_lib) })
    local gauche = colonne({
        self:puce("thermometer", etat, 15, { taille_icone = S(20) }),
        texte_w(texte(th.actuelle_court), 44, { gras = true }),
    }, S(2))

    -- droite : consigne − valeur +
    local cote = S(56)
    local valeur = colonne({
        texte_w(texte(th.consigne_court), 30, { gras = true }),
        texte_w("consigne", 13),
    }, 0, "center")
    local droite = rangee({
        self:bouton_icone("minus", cote, function()
            self:action("Consigne…", "/api/thermostat", { delta = -0.5 })
        end, { rayon = math.floor(cote / 2) }),
        Carte:new{ contenu = valeur, largeur = S(96), hauteur_min = cote, centre = true, bordure = 0, marge_interne = 0 },
        self:bouton_icone("plus", cote, function()
            self:action("Consigne…", "/api/thermostat", { delta = 0.5 })
        end, { rayon = math.floor(cote / 2) }),
    }, S(4))

    local espace = interieur - gauche:getSize().w - droite:getSize().w
    local haut = rangee({ gauche, HorizontalSpan:new{ width = math.max(0, espace) }, droite })

    -- modes
    local modes = liste(th.modes)
    local puces = {}
    for _, m in ipairs(modes) do
        local id = texte(m.id)
        local actif = id == mode
        table.insert(puces, Carte:new{
            contenu = self:puce(texte(m.icone), texte(m.lib), 16, { gras = actif, blanc = actif, taille_icone = S(22) }),
            largeur = self:case(#modes, interieur),
            hauteur_min = S(46),
            centre = true,
            inverse = actif,
            bordure = Size.border.thin,
            rayon = S(23),
            marge_interne = S(4),
            callback = function()
                if not actif then self:action("Mode…", "/api/thermostat", { mode = id }) end
            end,
        })
    end
    local contenu = colonne({ haut, #puces > 0 and rangee(puces, self.ecart) or nil }, S(10))
    return Carte:new{ contenu = contenu, largeur = L, bordure = bordure, marge_interne = marge, rayon = S(14) }
end

function Panneau:tuile_appareil(a, largeur)
    local allume = a.allume == true
    local nom_icone = texte(a.icone) ~= "" and texte(a.icone) or "lightbulb"
    if nom_icone == "lightbulb" and not allume then nom_icone = "lightbulb_outline" end
    local interieur = Carte.interieur(largeur, Size.border.thick, S(6))
    local sous = texte(a.piece)
    if a.dispo == false then sous = "indisponible" end
    local contenu = colonne({
        icone(nom_icone, S(40), allume),
        VerticalSpan:new{ width = S(4) },
        texte_w(texte(a.court) ~= "" and texte(a.court) or texte(a.nom), 17,
            { gras = true, blanc = allume, largeur = interieur }),
        texte_w(sous, 13, { blanc = allume, largeur = interieur }),
    }, 0, "center")
    local id = texte(a.id)
    return Carte:new{
        contenu = contenu,
        largeur = largeur,
        hauteur_min = S(112),
        centre = true,
        inverse = allume,
        marge_interne = S(6),
        rayon = S(14),
        callback = function() self:action("Envoi…", "/api/basculer", { entity_id = id }) end,
    }
end

function Panneau:blocs_maison(d)
    local blocs = {}
    if type(d.thermostat) == "table" then
        table.insert(blocs, self:bloc_thermostat(d.thermostat))
    end

    local temps = {}
    for _, t in ipairs(liste(d.temperatures)) do
        table.insert(temps, self:puce("thermometer", texte(t.nom) .. " " .. texte(t.valeur_court), 16,
            { taille_icone = S(20) }))
    end
    if #temps > 0 then
        table.insert(blocs, rangee(temps, S(24)))
    end

    local appareils = {}
    for _, piece in ipairs(liste(d.pieces)) do
        for _, a in ipairs(liste(piece.appareils)) do table.insert(appareils, a) end
    end
    if #appareils > 0 then
        local allumes = 0
        for _, a in ipairs(appareils) do if a.allume == true then allumes = allumes + 1 end end
        table.insert(blocs, self:titre_section("Lumières et prises", nil,
            texte_w(allumes == 0 and "tout est éteint" or (allumes .. " allumé" .. (allumes > 1 and "s" or "")), 14)))
        local par_rangee = 3
        local largeur = self:case(par_rangee)
        for i = 1, #appareils, par_rangee do
            local cases = {}
            for j = i, math.min(i + par_rangee - 1, #appareils) do
                table.insert(cases, self:tuile_appareil(appareils[j], largeur))
            end
            table.insert(blocs, rangee(cases, self.ecart))
        end
    end
    return blocs
end

-- --------------------------------------------------------------- onglet Repas

function Panneau:blocs_repas(d)
    local blocs = {}
    if self.vues.repas == "semaine" then
        table.insert(blocs, self:barre_nav(texte(d.titre), nil, d.precedente, d.suivante, true))
        for _, j in ipairs(liste(d.jours)) do
            table.insert(blocs, self:titre_section(texte(j.titre), nil,
                j.aujourdhui == true and self:etiquette("aujourd'hui") or nil))
            local repas = liste(j.repas)
            if #repas == 0 then table.insert(blocs, self:vide("Rien de prévu.")) end
            for _, r in ipairs(repas) do
                local id, jour = r.id, texte(r.d)
                table.insert(blocs, self:ligne_liste{
                    icone = texte(r.icone), taille_icone = 24,
                    lignes = { { texte = texte(r.titre) .. (r.option == true and "  (option)" or ""), taille = 16,
                                 gras = r.option ~= true } },
                    bordure = Size.border.thin, marge = S(6),
                    fait = texte(r.statut_lib) == "Fait",
                    callback = function() self:ouvrir_html("/api/recette", { repas = id, d = jour }) end,
                })
            end
        end
        return blocs
    end

    table.insert(blocs, self:barre_nav(texte(d.titre), d.aujourdhui == true and "aujourd'hui" or nil,
        d.precedent, d.suivant, false))
    local moments = liste(d.moments)
    if #moments == 0 then table.insert(blocs, self:vide("Aucun repas prévu ce jour-là.")) end
    for _, m in ipairs(moments) do
        table.insert(blocs, self:titre_section(texte(m.lib), texte(m.icone)))
        for _, r in ipairs(liste(m.repas)) do
            local id, jour = r.id, texte(r.d)
            table.insert(blocs, self:ligne_liste{
                lignes = {
                    { texte = texte(r.titre), taille = 19, gras = true },
                    { texte = joindre({ r.option == true and "Option" or "", texte(r.temps), texte(r.statut_lib) }), taille = 15 },
                    { texte = texte(r.portions), taille = 14 },
                    { texte = texte(r.note), taille = 14 },
                    { texte = texte(r.manque), taille = 14, gras = true },
                },
                bordure = r.option == true and Size.border.thin or Size.border.thick,
                fait = texte(r.statut_lib) == "Fait",
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
        table.insert(blocs, self:barre_nav(texte(d.titre), nil, d.precedente, d.suivante, true))
        for _, j in ipairs(liste(d.jours)) do
            table.insert(blocs, self:titre_section(texte(j.titre), nil,
                j.aujourdhui == true and self:etiquette("aujourd'hui") or nil))
            local seances = liste(j.seances)
            if #seances == 0 then table.insert(blocs, self:vide("Repos.")) end
            for _, s in ipairs(seances) do
                local a, id, jour = texte(s.athlete), texte(s.id), texte(s.d)
                table.insert(blocs, self:ligne_liste{
                    icone = texte(s.icone), taille_icone = 24,
                    lignes = { { texte = texte(s.nom) .. " · " .. texte(s.titre), taille = 16 } },
                    bordure = Size.border.thin, marge = S(6),
                    fait = s.statut == "realisee",
                    callback = function() self:ouvrir_html("/api/seance", { a = a, id = id, d = jour }) end,
                })
            end
        end
    else
        table.insert(blocs, self:barre_nav(texte(d.titre), d.aujourdhui == true and "aujourd'hui" or nil,
            d.precedent, d.suivant, false))
        for _, at in ipairs(liste(d.athletes)) do
            table.insert(blocs, self:titre_section(texte(at.nom), "account_multiple"))
            local seances = liste(at.seances)
            if #seances == 0 then table.insert(blocs, self:vide("Repos.")) end
            for _, s in ipairs(seances) do
                local a, id, jour = texte(at.id), texte(s.id), texte(s.d)
                table.insert(blocs, self:ligne_liste{
                    icone = texte(s.icone),
                    lignes = {
                        { texte = texte(s.titre), taille = 19, gras = true },
                        { texte = joindre({ texte(s.sport), texte(s.intensite), texte(s.fc), texte(s.commune) }), taille = 15 },
                        { texte = texte(s.resume), taille = 14 },
                        { texte = joindre({ texte(s.statut_lib), texte(s.fait), texte(s.rpe) }), taille = 14, gras = true },
                    },
                    fait = s.statut == "realisee",
                    callback = function() self:ouvrir_html("/api/seance", { a = a, id = id, d = jour }) end,
                })
            end
        end
    end
    for _, e in ipairs(liste(d.erreurs)) do
        table.insert(blocs, self:vide("Indisponible : " .. texte(e)))
    end
    return blocs
end

-- --------------------------------------------------------------- assemblage

function Panneau:entete()
    local M = self.marge
    local fermer = self:bouton_icone("close", S(44), function() self:fermer() end,
        { inverse = true, bordure = 0, taille_icone = S(28) })
    local sous = self.statut or (self.donnees.maison and texte(self.donnees.maison.date)) or os.date("%d/%m")
    if self.erreur then sous = "Erreur : " .. sous end

    local meteo = {}
    if type(self.meteo) == "table" then
        meteo = rangee({
            icone(texte(self.meteo.icone), S(44), true),
            colonne({
                texte_w(texte(self.meteo.temp_court), 26, { gras = true, blanc = true }),
                texte_w(texte(self.meteo.lib), 13, { blanc = true }),
            }, 0),
        }, S(8))
    end
    local largeur_droite = (meteo.getSize and meteo:getSize().w or 0) + fermer:getSize().w + S(12)
    local gauche_l = self.W - 2 * M - largeur_droite
    local gauche = Carte:new{
        contenu = colonne({
            texte_w(os.date("%H:%M"), 40, { gras = true, blanc = true }),
            texte_w(sous, 15, { blanc = true, gras = self.erreur, largeur = gauche_l }),
        }, 0),
        largeur = gauche_l,
        inverse = true,
        bordure = 0,
        rayon = 0,
        marge_interne = 0,
        callback = function() self:charger() end,  -- toucher l'heure : actualiser
    }
    local ligne = { HorizontalSpan:new{ width = M }, gauche }
    if meteo.getSize then table.insert(ligne, meteo) end
    table.insert(ligne, HorizontalSpan:new{ width = S(12) })
    table.insert(ligne, fermer)
    return FrameContainer:new{
        background = Blitbuffer.COLOR_BLACK,
        bordersize = 0,
        radius = 0,
        padding = 0,
        padding_top = S(8),
        padding_bottom = S(8),
        width = self.W,
        rangee(ligne, 0),
    }
end

function Panneau:pied()
    local boutons = {}
    for _, o in ipairs(ONGLETS) do
        local id = o.id
        local actif = id == self.onglet
        table.insert(boutons, self:bouton_icone(o.icone, S(62), function() self:changer_onglet(id) end, {
            libelle = o.lib, taille_libelle = 15, inverse = actif, taille_icone = S(28),
            largeur = self:case(#ONGLETS), bordure = actif and Size.border.thick or 0, rayon = S(14),
        }))
    end
    return colonne({
        LineWidget:new{ dimen = Geom:new{ w = self.W, h = Size.line.thin } },
        VerticalSpan:new{ width = S(6) },
        rangee({ HorizontalSpan:new{ width = self.marge }, rangee(boutons, self.ecart) }, 0),
        VerticalSpan:new{ width = S(6) },
    })
end

function Panneau:pagination()
    local cote = S(44)
    local milieu = self:largeur_utile() - 2 * cote - 2 * self.ecart
    return rangee({
        self:bouton_icone("chevron_left", cote, function() self:aller_page(self.page - 1) end,
            { bordure = Size.border.thin, rayon = math.floor(cote / 2) }),
        Carte:new{ contenu = texte_w(string.format("%d / %d", self.page, self.nb_pages), 16),
                   largeur = milieu, hauteur_min = cote, centre = true, bordure = 0, marge_interne = 0 },
        self:bouton_icone("chevron_right", cote, function() self:aller_page(self.page + 1) end,
            { bordure = Size.border.thin, rayon = math.floor(cote / 2) }),
    }, self.ecart)
end

function Panneau:paginer(blocs, h)
    local pages, courante, utilise = {}, {}, 0
    for _, b in ipairs(blocs) do
        local bh = b:getSize().h
        if #courante > 0 and utilise + self.ecart + bh > h then
            -- un titre de section ne reste pas seul en bas de page : il passe à la page suivante
            local orphelin
            if #courante > 1 and courante[#courante].est_titre then
                orphelin = table.remove(courante)
            end
            table.insert(pages, courante)
            courante, utilise = {}, 0
            if orphelin then
                table.insert(courante, orphelin)
                utilise = orphelin:getSize().h
            end
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
    self.h_entete = entete:getSize().h
    local h_dispo = self.H - self.h_entete - pied:getSize().h - 2 * self.ecart

    local blocs
    local d = self.donnees[self.onglet]
    if type(d) ~= "table" then
        blocs = { self:vide(self.erreur and ("Impossible de charger : " .. texte(self.statut)) or "Chargement…") }
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
            blocs = { self:vide("Affichage impossible : " .. tostring(res)) }
        end
    end

    local pages = self:paginer(blocs, h_dispo)
    local pagination
    if #pages > 1 then
        self.nb_pages = #pages
        local mesure = self:pagination():getSize().h + self.ecart
        pages = self:paginer(blocs, h_dispo - mesure)
    end
    self.nb_pages = #pages
    if self.page > self.nb_pages then self.page = self.nb_pages end
    if self.page < 1 then self.page = 1 end
    if self.nb_pages > 1 then pagination = self:pagination() end

    local corps = colonne(pages[self.page], self.ecart)
    local h_corps = h_dispo - (pagination and (pagination:getSize().h + self.ecart) or 0)

    local contenu = VerticalGroup:new{
        align = "left",
        entete,
        VerticalSpan:new{ width = self.ecart },
        rangee({
            HorizontalSpan:new{ width = self.marge },
            colonne({ corps, VerticalSpan:new{ width = math.max(0, h_corps - corps:getSize().h) } }),
        }, 0, "top"),
    }
    if pagination then
        table.insert(contenu, VerticalSpan:new{ width = self.ecart })
        table.insert(contenu, rangee({ HorizontalSpan:new{ width = self.marge }, pagination }, 0))
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
