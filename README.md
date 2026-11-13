# Unity Material Browser и Drop

Два дополнения Blender для материалов Unity:

- **Material Browser 0.13.3** создаёт локальную библиотеку материалов для Asset Browser.
- **Material Drop 0.5.3** собирает материал при перетаскивании файла `.mat` из Проводника Windows.

## Установка

Сборка рассчитана на **Windows x64 и Blender 5.2**. Скачайте оба архива на [странице Releases](https://github.com/Igrom4ik/UnityToBlender_MaterialBrowser/releases):

- `unity_material_browser-0.13.3-windows-x64.zip`
- `unity_material_drop-0.5.3-windows-x64.zip`

Установите оба через `Edit > Preferences > Get Extensions > Install from Disk`, затем полностью перезапустите Blender. Установленные дополнения используют одинаковое скомпилированное ядро; смешивать архивы разных выпусков не рекомендуется. Контрольные суммы также находятся в [`dist/SHA256SUMS`](dist/SHA256SUMS).

Инструкции: [Material Browser](docs/BROWSER_USAGE_RU.md), [Material Drop](docs/DROP_USAGE_RU.md). Ранее опубликованная версия для 3ds Max и её материалы остаются в репозитории; новые Blender-архивы не являются обновлением Max.

## Что поддерживается

Поддержаны материалы на основе стандартных шейдеров Unity и отдельные проектные шейдеры, включая `Custom/Environment/Unlit`, `Custom/Diffuse Bump Spec` и оконный материал с приближённым эффектом глубины. Поведение освещения и BRDF в Blender может отличаться от Unity. Оконный эффект зависит от UV и геометрии; в Browser есть команда подготовки атрибутов проёмов.

Если указанная в материале карта нормалей не найдена по GUID, для `Custom/Diffuse Bump Spec` строится материал с плоской нормалью и предупреждением. Чтобы вернуть рельеф, восстановите исходную ссылку в Unity или индекс доступных ассетов и повторите импорт. Аддоны читают Unity-проект и не изменяют его файлы.

## Исходники и сборки

`unity_material_browser/` и `unity_material_drop/` содержат исходники Blender-дополнений. Исходный Python-код ядра находится в `unity_pipeline_core/`. Локальные исходники дополнений обновлены до Browser 0.13.6 и Drop 0.5.6; их регистрация проверена в Blender 5.3.

Файлы `core/windows-x64/`, `dist/` и опубликованные архивы относятся к более раннему выпуску и не включают эти исходные изменения. Не выдавайте их за обновлённую сборку; новые архивы и скомпилированное ядро необходимо собрать и проверить отдельно.

Поддержаны адаптеры URP `Window_URP`, `Unlit_URP`, `PBR_PackedTextures` и detail-карты `Lit_BlendUVW2_Detail`. Fake depth окон требует специальных атрибутов UV4; визуальное совпадение с Unity не подтверждено. Для предпросмотра camera-relative offset в Material Drop доступна команда `Unity Camera Offset Preview`: необходимы камера сцены и объект с одним материалом, модификатор следует отключать перед экспортом.

Полная история изменений: [CHANGELOG.md](CHANGELOG.md).
