# EzRead 图标

采用已确认的墨绿展开书本设计。图标以暖白书页、中央细金色书签和墨绿圆角底表达科研阅读，采用克制的材质与柔和立体光影。

原始生成工具：内置 image_gen.imagegen，2026-09-30。此次品牌切换复用已确认源图，不重新生成设计。

文件：

- 源图：`assets/branding/ezread-book.png`。
- 应用 PNG：`static/ezread-icon.png`。
- Windows 多尺寸 ICO：`static/ezread.ico`。16、24、32、48、64、128、256 像素版本均由应用使用的高清原图 `static/ezread-icon.png` 直接缩放生成，保留书页层次、材质、阴影、金色书签和透明背景，使桌面快捷方式、任务栏与应用内图标保持相同设计。运行 `python assets/branding/build_windows_icon.py` 可重建，再运行 `powershell -File scripts/build-desktop.ps1` 更新 EXE 内嵌图标。
- 标题栏辅助 ICO：`static/window-icon-transparent.ico`，仅供原生小图标隐藏；网页 favicon 与任务栏仍使用真实书本图标。

`setup-desktop.ps1` 为桌面快捷方式在 `work/shortcut-icons/` 保存按图标内容摘要命名的同内容 ICO，避免 Windows 沿用旧图标缓存；该目录是本机生成文件，不提交。

提示词：

Create one finished app icon for EzRead, a scientific paper reader. Use case logo-brand. Apple-inspired macOS icon craftsmanship with immaculate geometry, gentle dimensionality, restrained material realism. One deep forest-green rounded square opaque tile fills 88 percent of a square canvas, straight-on view. Centered emblem: a beautifully simple open book made from exactly two broad warm-white folded paper wings, a clear central spine, slightly raised outer edges, with one thin muted golden bookmark descending at the central seam. The book should have a broad bold recognizable silhouette, not feathered page layers. Calm scholarly atmosphere, matte enamel green, warm ivory paper, soft top-left light, subtle shadows. No text or lines on pages. True transparent background outside tile. One icon only, no letters, no e, no browser swirl, no globe, no compass, no magnifier, no Apple logo, no mockup, no sparkle, no watermark. Square high-resolution image.
