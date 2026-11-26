--[[
Écrans d'action plein écran : valider un repas (quantités utilisées), déplacer une séance, saisir un ressenti.
Chaque écran a un en-tête (Retour + titre), un corps et une barre de boutons en bas.
]]

local Blitbuffer = require("ffi/blitbuffer")
local FrameContainer = require("ui/widget/container/framecontainer")
local Geom = require("ui/geometry")
local HorizontalGroup = require("ui/widget/horizontalgroup")
local HorizontalSpan = require("ui/widget/horizontalspan")
local InputContainer = require("ui/widget/container/inputcontainer")
local InputDialog = require("ui/widget/inputdialog")
local LineWidget = require("ui/widget/linewidget")
local Size = require("ui/size")
local UIManager = require("ui/uimanager")
local VerticalGroup = require("ui/widget/verticalgroup")
local VerticalSpan = require("ui/widget/verticalspan")
local Device = require("device")
local Screen = Device.screen

local UI = require("maison_carte")
local Carte = UI.Carte

local function S(x) return Screen:scaleBySize(x) end

local function rangee(widgets, ecart)
    local g = HorizontalGroup:new{ align = "center" }
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

local function nombre(x)
    if math.abs(x - math.floor(x + 0.5)) < 0.05 then
        return tostring(math.floor(x + 0.5))
    end
    return (string.format("%.1f", x):gsub("%.", ","))
end

-- ------------------------------------------------------------------ écran de base

local Ecran = InputContainer:extend{
    titre = "",
    on_close = nil,
    covers_fullscreen = true,
}

function Ecran:init()
    self.W, self.H = Screen:getWidth(), Screen:getHeight()
    self.dimen = Geom:new{ x = 0, y = 0, w = self.W, h = self.H }
    self.marge = S(12)
    self.ecart = S(10)
    self.L = self.W - 2 * self.marge
    if self.preparer then self:preparer() end
    self:construire()
end

-- Bouton : icône et/ou libellé
function Ecran:bouton(opts)
    local contenu
    local blanc = opts.inverse
    local gras = opts.gras ~= false
    local max_l = (opts.largeur or self.L) - S(16)
    if opts.icone and opts.libelle then
        local ic = S(opts.taille_icone or 24)
        contenu = rangee({ UI.icone(opts.icone, ic, blanc),
                           UI.texte(opts.libelle, opts.taille or 17, { gras = gras, blanc = blanc, largeur = max_l - ic - S(6) }) }, S(6))
    elseif opts.icone then
        contenu = UI.icone(opts.icone, S(opts.taille_icone or 26), blanc)
    else
        contenu = UI.texte(opts.libelle or "", opts.taille or 17, { gras = gras, blanc = blanc, largeur = max_l })
    end
    if opts.sous_libelle then
        contenu = colonne({ contenu, UI.texte(opts.sous_libelle, 12, { blanc = blanc, largeur = max_l }) }, 0, "center")
    end
    return Carte:new{
        contenu = contenu,
        largeur = opts.largeur,
        hauteur_min = opts.hauteur or S(52),
        centre = true,
        inverse = opts.inverse,
        bordure = opts.bordure,
        rayon = opts.rayon or S(12),
        marge_interne = S(4),
        callback = opts.callback,
    }
end

function Ecran:case(n, largeur)
    largeur = largeur or self.L
    return math.floor((largeur - (n - 1) * self.ecart) / n)
end

function Ecran:titre_section(t)
    return colonne({
        VerticalSpan:new{ width = S(4) },
        UI.texte(t, 17, { gras = true, largeur = self.L }),
        VerticalSpan:new{ width = S(4) },
        LineWidget:new{ dimen = Geom:new{ w = self.L, h = Size.line.thin } },
    })
end

function Ecran:construire()
    local retour = self:bouton{ icone = "chevron_left", libelle = "Retour", largeur = S(130), hauteur = S(48),
        bordure = Size.border.thin, rayon = S(24), callback = function() self:fermer() end }
    local entete = colonne({
        VerticalSpan:new{ width = self.marge },
        rangee({
            HorizontalSpan:new{ width = self.marge },
            retour,
            HorizontalSpan:new{ width = self.ecart },
            UI.texte(self.titre or "", 19, { gras = true, largeur = self.W - 3 * self.marge - retour:getSize().w }),
        }, 0),
        VerticalSpan:new{ width = self.marge },
        LineWidget:new{ dimen = Geom:new{ w = self.W, h = Size.line.thick } },
    })
    local boutons = self:boutons_pied() or {}
    local pied = colonne({
        LineWidget:new{ dimen = Geom:new{ w = self.W, h = Size.line.thin } },
        VerticalSpan:new{ width = S(8) },
        rangee({ HorizontalSpan:new{ width = self.marge }, rangee(boutons, self.ecart) }, 0),
        VerticalSpan:new{ width = S(10) },
    })
    local h_corps = self.H - entete:getSize().h - pied:getSize().h - 2 * self.ecart
    local corps = colonne(self:blocs(h_corps) or {}, self.ecart)
    self[1] = FrameContainer:new{
        background = Blitbuffer.COLOR_WHITE,
        bordersize = 0, padding = 0, margin = 0,
        width = self.W, height = self.H,
        colonne({
            entete,
            VerticalSpan:new{ width = self.ecart },
            rangee({ HorizontalSpan:new{ width = self.marge },
                     colonne({ corps, VerticalSpan:new{ width = math.max(0, h_corps - corps:getSize().h) } }) }, 0),
            VerticalSpan:new{ width = self.ecart },
            pied,
        }),
    }
end

function Ecran:rafraichir()
    self:construire()
    UIManager:setDirty(self, "ui")
end

function Ecran:fermer()
    UIManager:close(self)
    if self.on_close then self.on_close() end
end

function Ecran:onShow()
    UIManager:setDirty(self, "full")
    return true
end

function Ecran:onClose()
    self:fermer()
    return true
end

-- Saisie d'un texte libre au clavier
function Ecran:saisir_texte(titre, valeur, callback)
    local dlg
    dlg = InputDialog:new{
        title = titre,
        input = valeur or "",
        allow_newline = false,
        buttons = { {
            { text = "Annuler", id = "close", callback = function() UIManager:close(dlg) end },
            { text = "OK", is_enter_default = true, callback = function()
                local t = dlg:getInputText()
                UIManager:close(dlg)
                callback(t)
            end },
        } },
    }
    UIManager:show(dlg)
    dlg:onShowKeyboard()
end

-- ------------------------------------------------------------------ valider un repas

local EcranValidation = Ecran:extend{
    donnees = nil,     -- réponse de /api/repas/validation
    on_valider = nil,  -- function(lignes, note)
}

function EcranValidation:preparer()
    self.titre = self.donnees.titre or "Repas"
    self.lignes = {}
    for _, l in ipairs(type(self.donnees.lignes) == "table" and self.donnees.lignes or {}) do
        table.insert(self.lignes, {
            produit = l.produit, sens = l.sens, nom = tostring(l.nom or ""), unite = tostring(l.unite or ""),
            info = tostring(l.info or ""), quantite = tonumber(l.quantite) or 0, pas = tonumber(l.pas) or 1,
        })
    end
    self.note = ""
end

function EcranValidation:blocs()
    local blocs = {
        UI.paragraphe("Quantités retirées du stock. Corrige si tu as utilisé plus ou moins ; "
            .. "mets 0 à un ingrédient que tu n'as pas utilisé.", 14, self.L),
    }
    local cote = S(50)
    local valeur_l = S(92)
    local texte_l = self.L - 2 * cote - valeur_l - 3 * self.ecart
    for _, l in ipairs(self.lignes) do
        local ligne = l
        local unite = (ligne.unite == "pc" or ligne.unite == "portion") and "" or (" " .. ligne.unite)
        table.insert(blocs, rangee({
            colonne({
                UI.paragraphe(ligne.nom, 16, texte_l, { gras = true }),
                UI.texte(ligne.info, 13, { largeur = texte_l }),
            }, 0),
            self:bouton{ icone = "minus", largeur = cote, hauteur = cote, rayon = math.floor(cote / 2),
                bordure = Size.border.thin, callback = function()
                    ligne.quantite = math.max(0, ligne.quantite - ligne.pas)
                    self:rafraichir()
                end },
            Carte:new{ contenu = UI.texte(nombre(ligne.quantite) .. unite, 18, { gras = true }),
                largeur = valeur_l, hauteur_min = cote, centre = true, bordure = 0, marge_interne = 0 },
            self:bouton{ icone = "plus", largeur = cote, hauteur = cote, rayon = math.floor(cote / 2),
                bordure = Size.border.thin, callback = function()
                    ligne.quantite = ligne.quantite + ligne.pas
                    self:rafraichir()
                end },
        }, self.ecart))
    end
    table.insert(blocs, self:bouton{
        icone = "pencil", libelle = self.note ~= "" and ("Note : " .. self.note) or "Ajouter une note",
        largeur = self.L, hauteur = S(48), bordure = Size.border.thin, gras = false,
        callback = function()
            self:saisir_texte("Note (facultatif)", self.note, function(t)
                self.note = t or ""
                self:rafraichir()
            end)
        end,
    })
    return blocs
end

function EcranValidation:boutons_pied()
    return {
        self:bouton{ libelle = "Annuler", largeur = self:case(2), bordure = Size.border.thin,
            callback = function() self:fermer() end },
        self:bouton{ icone = "check_circle", libelle = "C'est fait", largeur = self:case(2), inverse = true,
            callback = function()
                local lignes = {}
                for _, l in ipairs(self.lignes) do
                    table.insert(lignes, { produit = l.produit, sens = l.sens, quantite = l.quantite })
                end
                self.on_valider(lignes, self.note)
            end },
    }
end

-- ------------------------------------------------------------------ déplacer une séance

local EcranDeplacer = Ecran:extend{
    cible = nil,         -- { titre, semaine = { {date, lib, actuel, aujourdhui} } }
    on_choisir = nil,    -- function(date)
}

function EcranDeplacer:preparer()
    self.titre = "Déplacer : " .. tostring(self.cible.titre or "")
end

function EcranDeplacer:blocs()
    local blocs = { UI.paragraphe("Choisis le nouveau jour (même semaine). Les séances en binôme sont "
        .. "déplacées pour les deux.", 15, self.L) }
    local jours = type(self.cible.semaine) == "table" and self.cible.semaine or {}
    local par_rangee = 4
    for i = 1, #jours, par_rangee do
        local cases = {}
        for j = i, math.min(i + par_rangee - 1, #jours) do
            local jour = jours[j]
            table.insert(cases, self:bouton{
                libelle = tostring(jour.lib or ""), taille = 18,
                sous_libelle = jour.actuel == true and "prévue" or (jour.aujourdhui == true and "aujourd'hui" or nil),
                largeur = self:case(par_rangee), hauteur = S(72),
                inverse = jour.actuel == true, bordure = Size.border.thick,
                callback = function()
                    if jour.actuel ~= true then self.on_choisir(tostring(jour.date)) end
                end,
            })
        end
        table.insert(blocs, rangee(cases, self.ecart))
    end
    return blocs
end

function EcranDeplacer:boutons_pied()
    return { self:bouton{ libelle = "Annuler", largeur = self.L, bordure = Size.border.thin,
        callback = function() self:fermer() end } }
end

-- ------------------------------------------------------------------ ressenti

local EcranRessenti = Ecran:extend{
    cible = nil,          -- { titre, ressenti = {rpe, douleurs, note}, douleurs = { {id, lib} } }
    on_enregistrer = nil, -- function(rpe, douleurs, note)
}

function EcranRessenti:preparer()
    self.titre = "Ressenti : " .. tostring(self.cible.titre or "")
    local r = type(self.cible.ressenti) == "table" and self.cible.ressenti or {}
    self.rpe = tonumber(r.rpe) or 0
    self.douleurs = {}
    for _, d in ipairs(type(r.douleurs) == "table" and r.douleurs or {}) do self.douleurs[tostring(d)] = true end
    self.note = type(r.note) == "string" and r.note or ""
end

function EcranRessenti:blocs()
    local blocs = { self:titre_section("Effort perçu (1 = très facile, 10 = maximal)") }
    for debut = 1, 10, 5 do
        local cases = {}
        for n = debut, debut + 4 do
            local valeur = n
            table.insert(cases, self:bouton{ libelle = tostring(n), taille = 20, largeur = self:case(5), hauteur = S(54),
                inverse = self.rpe == n, bordure = Size.border.thin, rayon = S(27),
                callback = function()
                    self.rpe = (self.rpe == valeur) and 0 or valeur
                    self:rafraichir()
                end })
        end
        table.insert(blocs, rangee(cases, self.ecart))
    end
    table.insert(blocs, self:titre_section("Douleurs"))
    local liste = type(self.cible.douleurs) == "table" and self.cible.douleurs or {}
    for i = 1, #liste, 3 do
        local cases = {}
        for j = i, math.min(i + 2, #liste) do
            local id = tostring(liste[j].id)
            table.insert(cases, self:bouton{ libelle = tostring(liste[j].lib), taille = 16, largeur = self:case(3),
                hauteur = S(46), inverse = self.douleurs[id] == true, bordure = Size.border.thin, rayon = S(23),
                callback = function()
                    self.douleurs[id] = not self.douleurs[id] or nil
                    self:rafraichir()
                end })
        end
        table.insert(blocs, rangee(cases, self.ecart))
    end
    table.insert(blocs, self:titre_section("Note"))
    table.insert(blocs, self:bouton{
        icone = "pencil", libelle = self.note ~= "" and self.note or "Écrire une note",
        largeur = self.L, hauteur = S(48), bordure = Size.border.thin, gras = false,
        callback = function()
            self:saisir_texte("Ressenti", self.note, function(t)
                self.note = t or ""
                self:rafraichir()
            end)
        end,
    })
    return blocs
end

function EcranRessenti:boutons_pied()
    return {
        self:bouton{ libelle = "Annuler", largeur = self:case(2), bordure = Size.border.thin,
            callback = function() self:fermer() end },
        self:bouton{ icone = "check_circle", libelle = "Enregistrer", largeur = self:case(2), inverse = true,
            callback = function()
                local d = {}
                for k in pairs(self.douleurs) do table.insert(d, k) end
                table.sort(d)
                self.on_enregistrer(self.rpe > 0 and self.rpe or nil, d, self.note)
            end },
    }
end

return {
    Ecran = Ecran,
    EcranValidation = EcranValidation,
    EcranDeplacer = EcranDeplacer,
    EcranRessenti = EcranRessenti,
}
