-- Unity Material Browser: put the tool in the main menu.
--
-- 3ds Max 2025 replaced the menu system and removed menuMan, so a menu is built
-- by answering the #cuiRegisterMenus callback with the CuiMenuManager it hands
-- over. This script lives in the user startup scripts folder and runs at every
-- start; the callback then fires whenever Max builds its menus.
--
-- ASCII only: MAXScript reads this as plain text.

global UMB_MENU_ID = #(1969001, 1969002)
global UMB_ACTION_ID = #(1969003, 1969004)
global UMB_MACRO_ACTION_TABLE = 647394

global UMB_registerMenu

fn UMB_registerMenu =
(
    local menuMgr = callbacks.notificationParam()
    if menuMgr == undefined do return false

    local mainMenuBar = menuMgr.mainMenuBar
    if mainMenuBar == undefined do return false

    -- A rebuilt menu bar starts empty, so this only ever adds one entry.
    local sub = mainMenuBar.CreateSubMenu UMB_MENU_ID "Unity Materials"
    sub.CreateAction UMB_ACTION_ID UMB_MACRO_ACTION_TABLE \
        "UnityMaterialBrowser_Build`Unity Material Browser"
    true
)

(
    -- Re-registering on every start would stack duplicates otherwise.
    callbacks.removeScripts id:#unityMaterialBrowser
    try
    (
        callbacks.addScript #cuiRegisterMenus "UMB_registerMenu()" id:#unityMaterialBrowser
        format "Unity Material Browser: menu registered\n"
    )
    catch
    (
        -- Older releases still have menuMan and never send this callback; there
        -- the action is reachable through Customize instead.
        format "Unity Material Browser: no menu callback here (%); use Customize > Toolbars\n" \
            (getCurrentException())
    )
)
