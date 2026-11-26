--[[
Carte : bloc encadré, éventuellement inversé (fond noir) et cliquable, contenant une ou plusieurs lignes
de texte. Sert de bouton, de case d'appareil et de ligne de liste dans le panneau.
]]

local Blitbuffer = require("ffi/blitbuffer")
local CenterContainer = require("ui/widget/container/centercontainer")
local Font = require("ui/font")
local FrameContainer = require("ui/widget/container/framecontainer")
local Geom = require("ui/geometry")
local GestureRange = require("ui/gesturerange")
local InputContainer = require("ui/widget/container/inputcontainer")
local LeftContainer = require("ui/widget/container/leftcontainer")
local Size = require("ui/size")
local TextBoxWidget = require("ui/widget/textboxwidget")
local VerticalGroup = require("ui/widget/verticalgroup")
local VerticalSpan = require("ui/widget/verticalspan")
local Device = require("device")
local Screen = Device.screen

local Carte = InputContainer:extend{
    lignes = nil,        -- { { texte = "...", taille = 20, gras = true, police = "cfont" }, ... }
    largeur = nil,
    hauteur_min = nil,
    inverse = false,     -- fond noir, texte blanc (appareil allumé, onglet actif…)
    centre = false,
    bordure = nil,
    rayon = nil,
    marge_interne = nil,
    callback = nil,
}

function Carte:init()
    local bordure = self.bordure or Size.border.thick
    local padding = self.marge_interne or Screen:scaleBySize(8)
    local fg = self.inverse and Blitbuffer.COLOR_WHITE or Blitbuffer.COLOR_BLACK
    local bg = self.inverse and Blitbuffer.COLOR_BLACK or Blitbuffer.COLOR_WHITE
    local interieur = self.largeur - 2 * (bordure + padding)

    local groupe = VerticalGroup:new{ align = self.centre and "center" or "left" }
    for i, l in ipairs(self.lignes or {}) do
        if l.texte and l.texte ~= "" then
            if i > 1 and #groupe > 0 then
                table.insert(groupe, VerticalSpan:new{ width = Screen:scaleBySize(2) })
            end
            table.insert(groupe, TextBoxWidget:new{
                text = l.texte,
                face = Font:getFace(l.police or "cfont", l.taille or 20),
                bold = l.gras,
                fgcolor = fg,
                bgcolor = bg,
                width = interieur,
                alignment = self.centre and "center" or "left",
            })
        end
    end

    local h = groupe:getSize().h
    if self.hauteur_min then
        h = math.max(h, self.hauteur_min - 2 * (bordure + padding))
    end
    local Conteneur = self.centre and CenterContainer or LeftContainer
    local contenu = Conteneur:new{
        dimen = Geom:new{ w = interieur, h = h },
        groupe,
    }

    self[1] = FrameContainer:new{
        bordersize = bordure,
        radius = self.rayon or Size.radius.button,
        padding = padding,
        margin = 0,
        background = bg,
        contenu,
    }
    local taille = self[1]:getSize()
    self.dimen = Geom:new{ x = 0, y = 0, w = taille.w, h = taille.h }

    if self.callback then
        self.ges_events = {
            TapCarte = {
                GestureRange:new{ ges = "tap", range = self.dimen },
            },
        }
    end
end

function Carte:onTapCarte()
    if self.callback then
        self.callback()
    end
    return true
end

return Carte
