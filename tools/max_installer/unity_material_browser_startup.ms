-- Unity Material Browser: put the tool in the main menu.
--
-- 3ds Max 2025 replaced the menu system and removed menuMan, so a menu is built
-- by answering the #cuiRegisterMenus callback with the CuiMenuManager it hands
-- over. This script lives in the user startup scripts folder and runs at every
-- start; the callback then fires whenever Max builds its menus.
--
-- The ids are GUID strings, not numbers: CreateSubMenu and CreateAction are
-- interface methods and reject anything else. They must also stay fixed, since
-- that is how Max recognises the same menu across sessions.
--
-- ASCII only: MAXScript reads this as plain text.

global UMB_MENU_ID = "7B2F5B1E-1F2A-4C58-9E44-0F0A1D2C3B41"
global UMB_ACTION_ID = "9C1D3E77-5A44-42B7-8D9E-6C4F2A8B10C3"
global UMB_MACRO_TABLE = 647394
global UMB_MACRO_ACTION = "UnityMaterialBrowser_Build`Unity Material Browser"

global UMB_logMenu
global UMB_registerMenu

-- A failure here happens during startup, where nobody is watching the listener,
-- so it goes to a file next to the install log. Appending rather than replacing
-- keeps the order of events: the script loading and the menu being built are
-- two separate moments, and which one is missing is the whole diagnosis.
fn UMB_logMenu text =
(
    try
    (
        local logPath = (trimRight (pathConfig.getDir #userScripts) "\\") + \
            "\\unity_material_max\\menu.log"
        local logFile = openFile logPath mode:"a+"
        if logFile == undefined do logFile = createFile logPath
        format "% %\n" (localTime) text to:logFile
        close logFile
    )
    catch ()
    format "Unity Material Browser: %\n" text
)

fn UMB_registerMenu =
(
    local menuMgr = callbacks.notificationParam()
    if menuMgr == undefined do return false

    local mainMenuBar = menuMgr.mainMenuBar
    if mainMenuBar == undefined do return false

    local done = false
    try
    (
        -- A rebuilt menu bar starts empty, so this only ever adds one entry.
        local sub = mainMenuBar.CreateSubMenu UMB_MENU_ID "Unity Materials"
        sub.CreateAction UMB_ACTION_ID UMB_MACRO_TABLE UMB_MACRO_ACTION
        done = true
        UMB_logMenu "menu created"
    )
    catch
    (
        UMB_logMenu ("menu FAILED: " + (getCurrentException()))
    )
    done
)

(
    -- Re-registering on every start would stack duplicates otherwise.
    callbacks.removeScripts id:#unityMaterialBrowser
    try
    (
        callbacks.addScript #cuiRegisterMenus "UMB_registerMenu()" id:#unityMaterialBrowser
        UMB_logMenu "startup script ran, menu callback registered"

        -- Startup scripts run after #cuiRegisterMenus has already fired, so
        -- registering alone leaves the menu bar as it was until something
        -- rebuilds it. Reloading the current configuration makes the callback
        -- happen now, in this session.
        local menuMgr = maxOps.GetICuiMenuMgr()
        if menuMgr != undefined then
        (
            menuMgr.LoadConfiguration (menuMgr.GetCurrentConfiguration())
            UMB_logMenu "menu configuration reloaded"
        )
        else UMB_logMenu "no menu manager here (batch or an older release)"
    )
    catch
    (
        -- Releases before 2025 still have menuMan and never send this callback;
        -- there the action is reachable through Customize instead.
        UMB_logMenu ("no menu callback here (" + (getCurrentException()) + "); use Customize > Toolbars")
    )
)
