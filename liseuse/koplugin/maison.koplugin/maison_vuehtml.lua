--[[
Affichage plein écran d'un contenu HTML (recette, détail de séance), avec défilement au doigt.
]]

local Blitbuffer = require("ffi/blitbuffer")
local Font = require("ui/font")
local FrameContainer = require("ui/widget/container/framecontainer")
local Geom = require("ui/geometry")
local HorizontalGroup = require("ui/widget/horizontalgroup")
local HorizontalSpan = require("ui/widget/horizontalspan")
local InputContainer = require("ui/widget/container/inputcontainer")
local LineWidget = require("ui/widget/linewidget")
local ScrollHtmlWidget = require("ui/widget/scrollhtmlwidget")
local Size = require("ui/size")
local TextWidget = require("ui/widget/textwidget")
local UIManager = require("ui/uimanager")
local VerticalGroup = require("ui/widget/verticalgroup")
local VerticalSpan = require("ui/widget/verticalspan")
local CenterContainer = require("ui/widget/container/centercontainer")
local Device = require("device")
local Screen = Device.screen

local Carte = require("maison_carte")

local VueHtml = InputContainer:extend{
    titre = "",
    html = "",
    css = "",
    on_close = nil,
    covers_fullscreen = true,
}

function VueHtml:init()
    local W, H = Screen:getWidth(), Screen:getHeight()
    self.dimen = Geom:new{ x = 0, y = 0, w = W, h = H }
    local marge = Screen:scaleBySize(10)

    local retour = Carte:new{
        lignes = { { texte = "‹ Retour", taille = 20, gras = true } },
        largeur = Screen:scaleBySize(130),
        centre = true,
        callback = function() self:fermer() end,
    }
    local titre = TextWidget:new{
        text = self.titre or "",
        face = Font:getFace("cfont", 20),
        bold = true,
        max_width = W - 3 * marge - retour:getSize().w,
    }
    local entete = VerticalGroup:new{
        align = "left",
        VerticalSpan:new{ width = marge },
        HorizontalGroup:new{
            align = "center",
            HorizontalSpan:new{ width = marge },
            retour,
            HorizontalSpan:new{ width = marge },
            titre,
        },
        VerticalSpan:new{ width = marge },
        LineWidget:new{ dimen = Geom:new{ w = W, h = Size.line.thick } },
    }

    local h_corps = H - entete:getSize().h - 2 * marge
    self.contenu = ScrollHtmlWidget:new{
        html_body = self.html,
        css = self.css,
        default_font_size = Screen:scaleBySize(20),
        width = W - 2 * marge,
        height = h_corps,
        dialog = self,
    }

    self[1] = FrameContainer:new{
        background = Blitbuffer.COLOR_WHITE,
        bordersize = 0,
        padding = 0,
        margin = 0,
        VerticalGroup:new{
            align = "left",
            entete,
            VerticalSpan:new{ width = marge },
            CenterContainer:new{
                dimen = Geom:new{ w = W, h = h_corps },
                self.contenu,
            },
        },
    }
end

function VueHtml:fermer()
    UIManager:close(self)
    if self.on_close then
        self.on_close()
    end
end

function VueHtml:onShow()
    UIManager:setDirty(self, "full")
    return true
end

function VueHtml:onClose()
    self:fermer()
    return true
end

return VueHtml
