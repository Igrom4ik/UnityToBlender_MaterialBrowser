# unity_material_max

Фронтенд 3ds Max к общему ядру `unity_pipeline_core`. План переноса —
[`docs/MAX_PORT_RU.md`](../docs/MAX_PORT_RU.md).

Кода пока нет: каталог заведён, чтобы seams были записаны там, где будет лежать
реализация.

## Что здесь появится

| Модуль | Ответственность | Импортирует `pymxs` |
| --- | --- | --- |
| `material_builder.py` | `ConversionPlan` → Physical Material: параметры, Bitmap-текстуры, распаковка `_NMG` | да |
| `library_build.py` | `materialLibrary` → `saveTempMaterialLibrary`: `.mat` на каталог Unity | да |
| `batch_build.py` | точка входа для `3dsmaxbatch.exe`: сборка без интерфейса | да |
| `ui.py` | диалог PySide2/6: источники, папка библиотеки, сборка, отчёт | да |
| `settings.py` | настройки в `%LOCALAPPDATA%`, а не в сцене | нет |
| `core/` | копия ядра, кладётся сборкой `tools/build_max_plugin.py` | нет |

Фаза A (чтение Unity → JSON) не требует Max вообще: это чистый Python из ядра.
Max нужен только для фазы B, и она рассчитана на запуск в фоне.

## Правило слоёв

Ядро не знает ни про Blender, ни про Max: `unity_pipeline_core` не импортирует
ни `bpy`, ни `pymxs`. Всё, что специфично для рендерера, живёт здесь и получает
от ядра готовый `ConversionPlan`.

## Как запускать во время разработки

3ds Max 2024–2026, Python 3.9+. Ядро совместимо с 3.7, поэтому копируется как
есть — проверено AST-сканом на отсутствие конструкций новее 3.7.

```python
# В листенере Max, до появления сборщика пакета:
import sys
sys.path.insert(0, r"D:\Blender_Unity_Material_Lib")
from unity_pipeline_core import extract
print(len(extract.load_index(r"D:\UnityMaterialLib")["materials"]))
```

Это и есть проверка M0: ядро внутри Max читает дерево, собранное
Blender-версией.
