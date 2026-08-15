-- Unity Material Browser: open the window with the four steps.
-- Lives in the user macros folder, so Customize > Toolbars finds it under the
-- category "Unity Material Browser" and it survives a restart.
--
-- ASCII only on purpose: 3ds Max reads this header as plain text, and a stray
-- encoding turns the whole macro into a syntax error before it runs.

macroScript UnityMaterialBrowser_Build
    category:"Unity Material Browser"
    buttonText:"Unity Materials"
    toolTip:"Unity Materials: scan Unity, build .mat libraries, open them in Max"
(
    -- A second comment marker inside one line breaks this file: the parser
    -- stops treating the line as a comment and reports "expected macroScript".
    -- Found by 3ds Max 2027 refusing to load the file at all, so every comment
    -- here carries its marker once, at the start.
    --
    -- The window itself is Python, in unity_material_max/dialog.py. It has to
    -- explain what to choose, and this file cannot hold that text: MAXScript
    -- reads it as ASCII. The macro only opens it.
    --
    -- The macro name and the category are also the menu action, so both are
    -- fixed: the startup script builds the menu from them.
    on execute do
    (
        -- `scripts` and `plugin` are taken by MAXScript itself; a clash here
        -- is a syntax error that kills the whole macro file.
        local scriptDir = trimRight (pathConfig.getDir #userScripts) "\\"

        -- ExecuteFile does not cache, so the launcher re-reads the plugin from
        -- disk on every click and an update lands without restarting Max.
        local launcher = scriptDir + "\\unity_material_browser_launch.py"

        local code = "import sys\n"
        code += "folder = r'" + scriptDir + "'\n"
        -- Clicking the button twice must not grow sys.path twice.
        code += "if folder not in sys.path:\n"
        code += "    sys.path.insert(0, folder)\n"
        code += "from unity_material_max import dialog\n"
        code += "dialog.show()\n"

        try
        (
            if doesFileExist launcher then python.ExecuteFile launcher
            else python.Execute code
        )
        catch
        (
            local reason = getCurrentException()
            messageBox ("The Unity Materials window did not open.\n\n" + reason + \
                "\n\nScripts folder:\n" + scriptDir + \
                "\n\nThe window needs PySide, which 3ds Max ships with. Without it the " + \
                "libraries can still be built from unity_material_max\\batch_build.py.") \
                title:"Unity Material Browser"
        )
    )
)
