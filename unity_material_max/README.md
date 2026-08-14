# unity_material_max

Фронтенд 3ds Max к общему ядру `unity_pipeline_core`. План переноса —
[`docs/MAX_PORT_RU.md`](../docs/MAX_PORT_RU.md).

Готово: построение Physical Material из `ConversionPlan` и запись
`.mat`-библиотек по каталогам Unity, включая запуск без интерфейса.

## Модули

| Модуль | Ответственность | Импортирует `pymxs` |
| --- | --- | --- |
| `recipe.py` | `ConversionPlan` → рецепт Physical Material: слоты, гамма, тайлинг, что не воспроизводится | **нет** |
| `material_builder.py` | рецепт → материал в Max | да |
| `library_build.py` | обход JSON-дерева, `.mat` на каталог, отчёт | да, внутри функций |
| `batch_build.py` | точка входа для `3dsmaxbatch.exe` | да, внутри функций |

Решает `recipe.py`, и он покрыт обычными тестами (`tests/test_max_recipe.py`) —
`pymxs` нужен только чтобы рецепт выполнить. Ядро не знает ни про Blender, ни
про Max.

## Установка

Из `dist/unity_material_max-<версия>.zip` распакуйте **обе** папки рядом друг с
другом в каталог скриптов Max, например:

```text
%LOCALAPPDATA%\Autodesk\3dsMax\<версия>\ENU\scripts\
├─ unity_material_max\
└─ unity_pipeline_core\
```

Ядро не вендорится внутрь плагина, как в Blender-версии: у Max нет формата
расширения, владеющего одной папкой, поэтому пакеты лежат рядом и импорт
`unity_pipeline_core` работает одинаково и в репозитории, и после установки.

## Сборка библиотеки без интерфейса

Извлечение (фаза A) 3ds Max не требует вообще — это чистый Python, и его делает
Blender-версия или скрипт на ядре. Фаза B:

```bat
3dsmaxbatch.exe "%LOCALAPPDATA%\Autodesk\3dsMax\2026\ENU\scripts\unity_material_max\batch_build.py" ^
    -mxsString "library:D:\UnityMaterialLib"
```

Результат — `D:\UnityMaterialLib\maxlib\<каталог>.mat` и отчёт
`_ump_max_report.json` рядом с индексом. `.mat` открывается в Material/Map
Browser; текстуры остаются в `Assets/`, ничего не копируется.

## Что ещё не воспроизводится

Пишется в отчёт, а не подгоняется молча:

- распаковка `_NMG` — подключается только нормаль, metallic и gloss из B/A ещё нет;
- окклюзия — у Physical Material нет слота, нужен композит в base color;
- подтверждённый тинт — нужен Color Correction поверх базовой карты;
- слои 2–4 блендинг-шейдеров, как и в Blender-версии.

## Проверка

`tests/test_max_recipe.py` гоняется обычным `unittest` без Max. Внутри Max
проверяется вручную по M0/M1 из плана; автотестов там не будет, пока нет
машины с лицензией.
