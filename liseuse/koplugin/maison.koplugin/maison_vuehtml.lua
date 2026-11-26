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

local UI = require("maison_carte")
local Carte = UI.Carte

local VueHtml = InputContainer:extend{
    titre = "",
    html = "",
    css = "",
    on_close = nil,
    actions = nil,     -- { {id, lib, icone, principal}, ... } : boutons en bas de l'écran
    on_action = nil,   -- function(action, vue)
    covers_fullscreen = true,
}

function VueHtml:init()
    local W, H = Screen:getWidth(), Screen:getHeight()
    self.dimen = Geom:new{ x = 0, y = 0, w = W, h = H }
    local marge = Screen:scaleBySize(10)

    local retour = Carte:new{
        contenu = HorizontalGroup:new{
            align = "center",
            UI.icone("chevron_left", Screen:scaleBySize(26)),
            UI.texte("Retour", 18, { gras = true }),
        },
        largeur = Screen:scaleBySize(130),
        hauteur_min = Screen:scaleBySize(48),
        centre = true,
        bordure = Size.border.thin,
        rayon = Screen:scaleBySize(24),
        marge_interne = Screen:scaleBySize(4),
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

    -- barre d'actions (valider un repas, déplacer une séance…)
    local barre
    local actions = type(self.actions) == "table" and self.actions or {}
    if #actions > 0 then
        local ecart = marge
        local n = #actions
        local largeur = math.floor((W - 2 * marge - (n - 1) * ecart) / n)
        local boutons = HorizontalGroup:new{ align = "center" }
        for i, a in ipairs(actions) do
            if i > 1 then table.insert(boutons, HorizontalSpan:new{ width = ecart }) end
            local action = a
            local blanc = a.principal == true
            local ic = Screen:scaleBySize(24)
            table.insert(boutons, Carte:new{
                contenu = HorizontalGroup:new{
                    align = "center",
                    UI.icone(tostring(a.icone or "check_circle"), ic, blanc),
                    HorizontalSpan:new{ width = Screen:scaleBySize(6) },
                    UI.texte(tostring(a.lib or ""), 16, { gras = true, blanc = blanc,
                        largeur = largeur - ic - Screen:scaleBySize(20) }),
                },
                largeur = largeur,
                hauteur_min = Screen:scaleBySize(54),
                centre = true,
                inverse = blanc,
                bordure = Size.border.thick,
                rayon = Screen:scaleBySize(12),
                marge_interne = Screen:scaleBySize(4),
                callback = function() if self.on_action then self.on_action(action, self) end end,
            })
        end
        barre = VerticalGroup:new{
            align = "left",
            LineWidget:new{ dimen = Geom:new{ w = W, h = Size.line.thin } },
            VerticalSpan:new{ width = marge },
            HorizontalGroup:new{ HorizontalSpan:new{ width = marge }, boutons },
            VerticalSpan:new{ width = marge },
        }
    end

    local h_corps = H - entete:getSize().h - 2 * marge - (barre and barre:getSize().h or 0)
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
            VerticalSpan:new{ width = marge },
            barre,
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
