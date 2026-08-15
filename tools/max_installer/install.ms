-- Unity Material Browser: post-install step of the .mzp package.
--
-- mzp.run has already copied both Python packages and the startup script into
-- the user scripts folder, and the macro into the user macros folder. This puts
-- the startup script where 3ds Max runs it, checks the result, and leaves a log
-- on disk -- a silent installer is indistinguishable from one that never ran.
--
-- Variable names avoid `plugin`, `macros` and friends: `plugin` is a MAXScript
-- keyword, and a name clash here is a syntax error that kills the whole file
-- before a single line runs.
--
-- Dialogs are suppressed in quiet mode, so the log is the record that always
-- exists. ASCII only: MAXScript reads this as plain text.

(
    local scriptDir = trimRight (pathConfig.getDir #userScripts) "\\"
    local macroDir = trimRight (pathConfig.getDir #userMacros) "\\"
    local startupDir = trimRight (pathConfig.getDir #userStartupScripts) "\\"

    local logPath = scriptDir + "\\unity_material_max\\install.log"
    local logFile = createFile logPath
    local report = ""

    format "Unity Material Browser install\n" to:logFile
    format "userScripts:        %\n" scriptDir to:logFile
    format "userMacros:         %\n" macroDir to:logFile
    format "userStartupScripts: %\n" startupDir to:logFile

    -- The startup script has to sit in the startup folder to be run at launch.
    local startupSource = scriptDir + "\\unity_material_browser_startup.ms"
    local startupTarget = startupDir + "\\unity_material_browser_startup.ms"
    if doesFileExist startupSource then
    (
        deleteFile startupTarget
        if (copyFile startupSource startupTarget) then
        (
            deleteFile startupSource
            format "startup script: installed\n" to:logFile
        )
        else format "startup script: COPY FAILED to %\n" startupTarget to:logFile
    )
    else format "startup script: missing from the package\n" to:logFile

    local pluginFile = scriptDir + "\\unity_material_max\\library_build.py"
    local coreFile = scriptDir + "\\unity_pipeline_core\\extract.py"
    local macroFile = macroDir + "\\UnityMaterialBrowser.mcr"

    format "plugin file:  %\n" (doesFileExist pluginFile) to:logFile
    format "core file:    %\n" (doesFileExist coreFile) to:logFile
    format "macro file:   %\n" (doesFileExist macroFile) to:logFile
    format "startup file: %\n" (doesFileExist startupTarget) to:logFile

    if not ((doesFileExist pluginFile) and (doesFileExist coreFile)) then
    (
        report = "Install FAILED: the Python packages did not reach\n" + scriptDir
        format "install: FAILED\n" to:logFile
    )
    else
    (
        -- Make the macro usable now instead of after the next restart.
        if doesFileExist macroFile do
        (
            try (fileIn macroFile)
            catch (format "fileIn macro failed: %\n" (getCurrentException()) to:logFile)
        )

        -- The menu is built by a callback that fires when Max builds its menus,
        -- so it shows up at the next start; running the script now registers the
        -- callback in this session as well.
        if doesFileExist startupTarget do
        (
            try (fileIn startupTarget)
            catch (format "fileIn startup failed: %\n" (getCurrentException()) to:logFile)
        )

        local probe = "import sys\n"
        probe += "sys.path.insert(0, r'" + scriptDir + "')\n"
        probe += "import unity_pipeline_core, unity_material_max\n"
        probe += "print('Unity Material Browser: core %s, plugin %s' % (unity_pipeline_core.__version__, unity_material_max.__version__))\n"
        try
        (
            python.Execute probe
            format "python import: ok\n" to:logFile
        )
        catch (format "python import FAILED: %\n" (getCurrentException()) to:logFile)

        report = "Unity Material Browser installed.\n\n"
        report += "Restart 3ds Max: the menu \"Unity Materials\" appears in the main menu bar.\n"
        report += "It opens a window with the four steps: library folder, scan Unity,\n"
        report += "build .mat, open them in the Material Editor.\n"
        report += "It is also in Customize > Toolbars, category \"Unity Material Browser\".\n\n"
        report += "Scripts: " + scriptDir + "\n"
        report += "Log:     " + logPath
        format "install: ok\n" to:logFile
    )

    close logFile
    format "%\n" report
    -- Suppressed automatically in quiet and batch mode, which is why the log
    -- above exists.
    messageBox report title:"Unity Material Browser"
)
