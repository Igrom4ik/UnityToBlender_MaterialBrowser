-- Unity Material Browser: build .mat libraries from an extracted Unity library.
-- Lives in the user macros folder, so Customize > Toolbars finds it under the
-- category "Unity Material Browser" and it survives a restart.
--
-- ASCII only on purpose: MAXScript files are read as plain text and a stray
-- encoding turns the whole macro into a syntax error.

macroScript UnityMaterialBrowser_Build
    category:"Unity Material Browser"
    buttonText:"Build .mat"
    toolTip:"Build 3ds Max material libraries from a Unity library folder"
(
    on execute do
    (
        local root = getSavePath caption:"Library folder (the one holding _ump_index.json)"
        if root != undefined do
        (
            local scripts = trimRight (pathConfig.getDir #userScripts) "\\"
            local target = trimRight root "\\"

            if not (doesFileExist (target + "\\_ump_index.json")) then
            (
                messageBox "No _ump_index.json here.\n\nRun the extract phase first: it needs no 3ds Max." \
                    title:"Unity Material Browser"
            )
            else
            (
                local code = "import sys\n"
                code += "sys.path.insert(0, r'" + scripts + "')\n"
                code += "from unity_material_max.library_build import build_library\n"
                code += "result = build_library(r'" + target + "')\n"
                code += "print('Unity Material Browser: built %d materials into %d libraries, failed %d' % (result.built, result.libraries, result.failed))\n"
                python.Execute code
                messageBox ("Done. Libraries are in\n" + target + "\\maxlib\n\nOpen one from the Material/Map Browser.") \
                    title:"Unity Material Browser"
            )
        )
    )
)
