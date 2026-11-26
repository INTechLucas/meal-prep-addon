--[[
Briques graphiques du panneau :
- Carte : bloc arrondi, éventuellement inversé (fond noir) et cliquable, autour d'un contenu quelconque ;
- UI.icone / UI.texte / UI.paragraphe : icônes SVG (noires ou blanches) et textes dans la bonne couleur.
]]

local Blitbuffer = require("ffi/blitbuffer")
local CenterContainer = require("ui/widget/container/centercontainer")
local Font = require("ui/font")
local FrameContainer = require("ui/widget/container/framecontainer")
local Geom = require("ui/geometry")
local GestureRange = require("ui/gesturerange")
local HorizontalSpan = require("ui/widget/horizontalspan")
local ImageWidget = require("ui/widget/imagewidget")
local InputContainer = require("ui/widget/container/inputcontainer")
local LeftContainer = require("ui/widget/container/leftcontainer")
local Size = require("ui/size")
local TextBoxWidget = require("ui/widget/textboxwidget")
local TextWidget = require("ui/widget/textwidget")
local Device = require("device")
local Screen = Device.screen
local logger = require("logger")

local UI = {
    dossier = nil,   -- dossier du plugin (icônes), fixé par main.lua
}

local function couleur(blanc)
    return blanc and Blitbuffer.COLOR_WHITE or Blitbuffer.COLOR_BLACK
end

-- Icône SVG du plugin (ic_<nom>_n.svg ou ic_<nom>_b.svg), carrée, de côté `taille` pixels
function UI.icone(nom, taille, blanc)
    local chemin = string.format("%s/ic_%s_%s.svg", UI.dossier or ".", nom or "", blanc and "b" or "n")
    local ok, w = pcall(function()
        local iw = ImageWidget:new{
            file = chemin,
            width = taille,
            height = taille,
            alpha = true,
        }
        iw:getSize()
        return iw
    end)
    if ok and w then return w end
    logger.warn("Maison: icône indisponible", chemin, w)
    return HorizontalSpan:new{ width = taille }
end

-- Texte sur une ligne (tronqué à `largeur` si donné)
function UI.texte(t, taille, opts)
    opts = opts or {}
    return TextWidget:new{
        text = t or "",
        face = Font:getFace(opts.police or "cfont", taille),
        bold = opts.gras,
        fgcolor = couleur(opts.blanc),
        max_width = opts.largeur,
    }
end

-- Texte sur plusieurs lignes, de largeur fixe
function UI.paragraphe(t, taille, largeur, opts)
    opts = opts or {}
    return TextBoxWidget:new{
        text = t or "",
        face = Font:getFace(opts.police or "cfont", taille),
        bold = opts.gras,
        fgcolor = couleur(opts.blanc),
        bgcolor = opts.blanc and Blitbuffer.COLOR_BLACK or Blitbuffer.COLOR_WHITE,
        width = largeur,
        alignment = opts.centre and "center" or "left",
    }
end

local Carte = InputContainer:extend{
    contenu = nil,       -- widget affiché dans la carte
    largeur = nil,       -- largeur totale (bordure comprise)
    hauteur_min = nil,   -- hauteur totale minimale
    inverse = false,     -- fond noir
    centre = false,      -- contenu centré (sinon à gauche, centré verticalement)
    bordure = nil,
    rayon = nil,
    marge_interne = nil,
    callback = nil,
}

-- Largeur utile à l'intérieur d'une carte de largeur totale `largeur`
function Carte.interieur(largeur, bordure, marge)
    return largeur - 2 * ((bordure or Size.border.thick) + (marge or Screen:scaleBySize(10)))
end

function Carte:init()
    local bordure = self.bordure or Size.border.thick
    local padding = self.marge_interne or Screen:scaleBySize(10)
    local interieur = self.largeur - 2 * (bordure + padding)
    local h = self.contenu:getSize().h
    if self.hauteur_min then
        h = math.max(h, self.hauteur_min - 2 * (bordure + padding))
    end
    local Conteneur = self.centre and CenterContainer or LeftContainer
    self[1] = FrameContainer:new{
        bordersize = bordure,
        radius = self.rayon or Screen:scaleBySize(12),
        padding = padding,
        margin = 0,
        background = self.inverse and Blitbuffer.COLOR_BLACK or Blitbuffer.COLOR_WHITE,
        Conteneur:new{
            dimen = Geom:new{ w = interieur, h = h },
            self.contenu,
        },
    }
    local taille = self[1]:getSize()
    self.dimen = Geom:new{ x = 0, y = 0, w = taille.w, h = taille.h }
    if self.callback then
        self.ges_events = {
            TapCarte = { GestureRange:new{ ges = "tap", range = self.dimen } },
        }
    end
end

function Carte:onTapCarte()
    if self.callback then self.callback() end
    return true
end

UI.Carte = Carte
return UI
