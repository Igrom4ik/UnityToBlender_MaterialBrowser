-- Unity Material Browser: post-install step of the .mzp package.
--
-- mzp.run has already copied both Python packages into the user scripts folder
-- and the macro into the user macros folder. This checks that it actually
-- landed, loads the macro so it works without restarting Max, and says where
-- everything went instead of finishing silently.
--
-- ASCII only: MAXScript reads these as plain text, and an encoding surprise
-- turns the installer into a syntax error at the worst possible moment.

(
    local scripts = trimRight (pathConfig.getDir #userScripts) "\\"
    local macros = trimRight (pathConfig.getDir #userMacros) "\\"

    local plugin = scripts + "\\unity_material_max\\library_build.py"
    local core = scripts + "\\unity_pipeline_core\\extract.py"
    local macro = macros + "\\UnityMaterialBrowser.mcr"

    local missing = #()
    if not (doesFileExist plugin) do append missing plugin
    if not (doesFileExist core) do append missing core

    if missing.count > 0 then
    (
        local report = "Install failed. These are missing:\n"
        for item in missing do report += ("\n" + item)
        format "%\n" report
        messageBox report title:"Unity Material Browser"
    )
    else
    (
        -- Make the tool usable now rather than after the next restart.
        if doesFileExist macro do fileIn macro

        -- The core must import before anything else is worth trying.
        local probe = "import sys\n"
        probe += "sys.path.insert(0, r'" + scripts + "')\n"
        probe += "import unity_pipeline_core, unity_material_max\n"
        probe += "print('Unity Material Browser: core %s, plugin %s' % (unity_pipeline_core.__version__, unity_material_max.__version__))\n"
        python.Execute probe

        local message = "Installed.\n\n"
        message += "Scripts: " + scripts + "\n"
        message += "Macro:   " + macro + "\n\n"
        message += "Customize > Toolbars, category \"Unity Material Browser\",\n"
        message += "action \"Build .mat\" - drag it onto a toolbar or a menu.\n\n"
        message += "Headless build:\n"
        message += "3dsmaxbatch.exe \"" + scripts + "\\unity_material_max\\batch_build.py\"\n"
        message += "    -mxsString \"library:<library folder>\""
        format "%\n" message
        messageBox message title:"Unity Material Browser"
    )
)
