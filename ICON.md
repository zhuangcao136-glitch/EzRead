# EzRead 图标

采用已确认的墨绿展开书本设计。图标以暖白书页、中央细金色书签和墨绿圆角底表达科研阅读，采用克制的材质与柔和立体光影。

原始生成工具：内置 image_gen.imagegen，2026-09-30。此次品牌切换复用已确认源图，不重新生成设计。

文件：

- 源图：`assets/branding/ezread-book.png`。
- 应用 PNG：`static/ezread-icon.png`。
- Windows 多尺寸 ICO：`static/ezread.ico`。所有尺寸采用所选图标的墨绿书本、暖白书页和金色书签轮廓，使用适合任务栏的纯色版本，避免 Windows 选择较大但柔和的原图再缩放。运行 `python assets/branding/build_windows_icon.py` 可重建；应用内展示仍使用已确认的原始图像。
- 标题栏辅助 ICO：`static/window-icon-transparent.ico`，仅供原生小图标隐藏；网页 favicon 与任务栏仍使用真实书本图标。

提示词：

Create one finished app icon for EzRead, a scientific paper reader. Use case logo-brand. Apple-inspired macOS icon craftsmanship with immaculate geometry, gentle dimensionality, restrained material realism. One deep forest-green rounded square opaque tile fills 88 percent of a square canvas, straight-on view. Centered emblem: a beautifully simple open book made from exactly two broad warm-white folded paper wings, a clear central spine, slightly raised outer edges, with one thin muted golden bookmark descending at the central seam. The book should have a broad bold recognizable silhouette, not feathered page layers. Calm scholarly atmosphere, matte enamel green, warm ivory paper, soft top-left light, subtle shadows. No text or lines on pages. True transparent background outside tile. One icon only, no letters, no e, no browser swirl, no globe, no compass, no magnifier, no Apple logo, no mockup, no sparkle, no watermark. Square high-resolution image.
