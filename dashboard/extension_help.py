"""Plain-English help for File-Type Explorer preset extensions."""

from __future__ import annotations

from typing import Any


EXTENSION_HELP: dict[str, dict[str, str]] = {
    ".pdf": {
        "name": "Portable Document Format",
        "icon": "PDF",
        "description": (
            "PDF files preserve a document's layout so it looks consistent on "
            "different computers and phones. They commonly contain manuals, "
            "invoices, forms, statements, ebooks, and exported reports. A PDF "
            "usually opens in a web browser or dedicated PDF reader."
        ),
        "example": "A downloaded electricity bill or an exported product support manual.",
    },
    ".doc": {
        "name": "Legacy Microsoft Word document",
        "icon": "DOC",
        "description": (
            "DOC is the older Microsoft Word document format used before DOCX "
            "became standard. It can contain formatted text, tables, pictures, "
            "and document settings. Older workplace files, templates, letters, "
            "and school assignments may still use this format."
        ),
        "example": "A resume created with Microsoft Word 2003 or an older template.",
    },
    ".docx": {
        "name": "Microsoft Word document",
        "icon": "DOCX",
        "description": (
            "DOCX is the modern Microsoft Word document format. It stores "
            "formatted text, images, tables, comments, and document structure in "
            "a compressed package. It is widely used for resumes, reports, "
            "letters, procedures, assignments, and other editable documents."
        ),
        "example": "An editable resume, university assignment, or internal support procedure.",
    },
    ".odt": {
        "name": "OpenDocument text document",
        "icon": "ODT",
        "description": (
            "ODT is an editable document format commonly created by LibreOffice "
            "Writer and other OpenDocument-compatible applications. It can hold "
            "formatted text, images, tables, and styles. It serves a similar role "
            "to DOCX but follows an open document standard."
        ),
        "example": "A letter or report edited in LibreOffice Writer instead of Word.",
    },
    ".rtf": {
        "name": "Rich Text Format document",
        "icon": "RTF",
        "description": (
            "RTF stores text with basic formatting such as fonts, bold text, "
            "colours, and simple images. Many word processors can open it, which "
            "made it useful for exchanging documents between applications. It is "
            "less feature-rich than modern DOCX files."
        ),
        "example": "A formatted letter shared between different word-processing programs.",
    },
    ".txt": {
        "name": "Plain text file",
        "icon": "TXT",
        "description": (
            "TXT files contain unformatted readable text without embedded images, "
            "fonts, or page layouts. They are used for notes, instructions, logs, "
            "configuration examples, and exported data. Although simple, some TXT "
            "files may still contain important application or project information."
        ),
        "example": "A readme note, copied troubleshooting steps, or exported log summary.",
    },
    ".ppt": {
        "name": "Legacy PowerPoint presentation",
        "icon": "PPT",
        "description": (
            "PPT is the older Microsoft PowerPoint presentation format. It can "
            "contain slides, text, images, charts, transitions, and speaker notes. "
            "Presentations created with older Office versions or downloaded from "
            "older course and workplace archives may use it."
        ),
        "example": "A training presentation originally created using PowerPoint 2003.",
    },
    ".pptx": {
        "name": "Microsoft PowerPoint presentation",
        "icon": "PPTX",
        "description": (
            "PPTX is the modern Microsoft PowerPoint format for editable slide "
            "presentations. It may contain text, pictures, charts, video, animation, "
            "and presenter notes. These files are common for meetings, portfolios, "
            "lessons, training sessions, and project demonstrations."
        ),
        "example": "A project presentation, training deck, or portfolio slideshow.",
    },
    ".xls": {
        "name": "Legacy Microsoft Excel workbook",
        "icon": "XLS",
        "description": (
            "XLS is the older Microsoft Excel spreadsheet format. It stores cells, "
            "formulas, formatting, charts, and multiple worksheets. Older budgets, "
            "inventories, exported business data, and calculation templates may use "
            "XLS instead of the newer XLSX format."
        ),
        "example": "An older household budget or archived workplace inventory spreadsheet.",
    },
    ".xlsx": {
        "name": "Microsoft Excel workbook",
        "icon": "XLSX",
        "description": (
            "XLSX is the modern Microsoft Excel workbook format. It can contain "
            "tables, formulas, charts, filters, and several worksheets in one file. "
            "It is commonly used for budgets, lists, reports, data analysis, "
            "schedules, inventories, and exported records."
        ),
        "example": "A monthly budget, job application tracker, or equipment inventory.",
    },
    ".mp4": {
        "name": "MPEG-4 video",
        "icon": "MP4",
        "description": (
            "MP4 is a widely supported video container that can store video, audio, "
            "subtitles, and related metadata. Phones, cameras, screen recorders, "
            "streaming downloads, and editing programs commonly create MP4 files. "
            "Long or high-resolution recordings can consume substantial storage."
        ),
        "example": "A phone recording, downloaded tutorial, or captured gameplay video.",
    },
    ".mkv": {
        "name": "Matroska video",
        "icon": "MKV",
        "description": (
            "MKV is a flexible video container often used for high-quality movies, "
            "recordings, and archived media. One file can contain several audio "
            "tracks, subtitle tracks, and chapters. MKV files may be large and may "
            "need a compatible player such as VLC."
        ),
        "example": "A high-quality movie containing multiple languages and subtitle tracks.",
    },
    ".avi": {
        "name": "Audio Video Interleave",
        "icon": "AVI",
        "description": (
            "AVI is an older Microsoft video container used by cameras, capture "
            "tools, and legacy editing software. Compression varies, so AVI files "
            "can be considerably larger than equivalent modern MP4 videos. Some "
            "older devices and applications still export this format."
        ),
        "example": "An old camera recording or video captured by legacy software.",
    },
    ".mov": {
        "name": "QuickTime movie",
        "icon": "MOV",
        "description": (
            "MOV is a video container associated with Apple QuickTime and commonly "
            "created by iPhones, cameras, and video-editing applications. It can hold "
            "high-quality video and audio, which often makes files large. Most modern "
            "Windows media players can open it."
        ),
        "example": "A high-resolution iPhone clip copied onto a Windows computer.",
    },
    ".wmv": {
        "name": "Windows Media Video",
        "icon": "WMV",
        "description": (
            "WMV is a Microsoft video format common in older Windows applications, "
            "email attachments, presentations, and downloaded media. It was designed "
            "to compress video for Windows playback and online delivery. Older "
            "training materials and archived recordings may use it."
        ),
        "example": "An archived Windows training clip or older emailed video.",
    },
    ".webm": {
        "name": "WebM video",
        "icon": "WEBM",
        "description": (
            "WebM is an open video format designed for web browsers and online "
            "media. Websites, screen-recording tools, chat applications, and web "
            "downloads may create it. WebM usually provides efficient compression, "
            "but long or high-resolution recordings can still be large."
        ),
        "example": "A browser-recorded demonstration or video downloaded from a website.",
    },
    ".mp3": {
        "name": "MP3 audio",
        "icon": "MP3",
        "description": (
            "MP3 is a common compressed audio format supported by almost every "
            "computer, phone, media player, and car stereo. It is used for music, "
            "podcasts, voice recordings, and downloaded audio. Quality and file size "
            "depend on the selected compression rate."
        ),
        "example": "A downloaded song, podcast episode, or compressed voice recording.",
    },
    ".wav": {
        "name": "Waveform audio",
        "icon": "WAV",
        "description": (
            "WAV commonly stores uncompressed or lightly compressed audio, preserving "
            "high sound quality at the cost of larger files. Recording software, "
            "audio editors, games, and Windows sound tools often use it. Raw recordings "
            "can consume storage quickly."
        ),
        "example": "An uncompressed microphone recording prepared for audio editing.",
    },
    ".flac": {
        "name": "Free Lossless Audio Codec",
        "icon": "FLAC",
        "description": (
            "FLAC compresses audio without discarding sound information, so it keeps "
            "the original quality while using less space than uncompressed WAV. Music "
            "collectors and audio archives often use it. FLAC files are usually larger "
            "than MP3 versions of the same recording."
        ),
        "example": "A lossless music album stored for high-quality listening or archiving.",
    },
    ".m4a": {
        "name": "MPEG-4 audio",
        "icon": "M4A",
        "description": (
            "M4A is an audio container frequently used by Apple devices, music stores, "
            "podcast applications, and voice recorders. It commonly contains AAC or "
            "lossless audio. It offers good quality at practical sizes and is supported "
            "by most modern players."
        ),
        "example": "A voice memo, purchased song, or downloaded podcast episode.",
    },
    ".aac": {
        "name": "Advanced Audio Coding",
        "icon": "AAC",
        "description": (
            "AAC is a compressed audio format used by streaming services, phones, "
            "games, and video applications. It often delivers better quality than MP3 "
            "at a similar size. An AAC file may be standalone or used as the audio "
            "track inside a video."
        ),
        "example": "A compressed mobile recording or audio exported from video software.",
    },
    ".jpg": {
        "name": "JPEG image",
        "icon": "JPG",
        "description": (
            "JPG is a compressed image format designed for photographs and detailed "
            "pictures. Phones, cameras, websites, social media, and image editors use "
            "it extensively. Compression keeps files manageable but slightly reduces "
            "quality each time an image is heavily recompressed."
        ),
        "example": "A camera photograph, website image, or downloaded product picture.",
    },
    ".jpeg": {
        "name": "JPEG image",
        "icon": "JPEG",
        "description": (
            "JPEG is the same image family as JPG; the longer extension is simply "
            "another filename convention. It is best suited to photographs and complex "
            "images, using lossy compression to reduce storage. Cameras, phones, web "
            "pages, and editors commonly create it."
        ),
        "example": "A phone photograph saved using the longer JPEG extension.",
    },
    ".png": {
        "name": "Portable Network Graphics",
        "icon": "PNG",
        "description": (
            "PNG is a lossless image format that preserves sharp text, interface "
            "graphics, and transparent backgrounds. Screenshots, logos, diagrams, web "
            "assets, and application graphics often use it. Photograph-like PNG files "
            "can be much larger than equivalent JPG images."
        ),
        "example": "A Windows screenshot, transparent logo, or sharp interface graphic.",
    },
    ".gif": {
        "name": "Graphics Interchange Format",
        "icon": "GIF",
        "description": (
            "GIF supports simple images and short looping animations using a limited "
            "colour palette. It is common in messages, websites, reaction images, and "
            "older web graphics. Long or high-resolution GIF animations can become "
            "surprisingly large compared with modern video formats."
        ),
        "example": "A looping reaction animation downloaded from a messaging website.",
    },
    ".webp": {
        "name": "WebP image",
        "icon": "WEBP",
        "description": (
            "WebP is a modern web image format supporting efficient compression, "
            "transparency, and animation. Browsers and websites commonly download or "
            "cache WebP images because they are often smaller than JPG or PNG. Modern "
            "image viewers and editors generally support it."
        ),
        "example": "A product image saved from a modern shopping website.",
    },
    ".heic": {
        "name": "High Efficiency Image Container",
        "icon": "HEIC",
        "description": (
            "HEIC stores high-quality photographs efficiently and is commonly used by "
            "iPhones and some modern cameras. It can preserve more image information "
            "than JPG at a smaller size. Windows may require an image extension or "
            "compatible application to open it."
        ),
        "example": "A photograph copied directly from a recent iPhone or camera.",
    },
    ".zip": {
        "name": "ZIP archive",
        "icon": "ZIP",
        "description": (
            "ZIP combines one or more files and folders into a compressed archive. It "
            "is commonly used for downloads, email attachments, backups, software "
            "packages, and grouped project files. Windows can open ZIP archives without "
            "additional software, but their contents may still be important."
        ),
        "example": "A downloaded project bundle or several documents compressed together.",
    },
    ".rar": {
        "name": "RAR archive",
        "icon": "RAR",
        "description": (
            "RAR is a compressed archive format often used for large downloads, media "
            "collections, and files split into several parts. Opening it usually "
            "requires software such as 7-Zip or WinRAR. Deleting one part of a divided "
            "archive can make the collection unusable."
        ),
        "example": "One part of a multi-file download named archive.part1.rar.",
    },
    ".7z": {
        "name": "7-Zip archive",
        "icon": "7Z",
        "description": (
            "7Z is a compressed archive format created for the 7-Zip application. It "
            "often achieves strong compression and may contain many files, encrypted "
            "content, or split archive parts. It is used for downloads, backups, "
            "software packages, and project transfers."
        ),
        "example": "A compressed backup or large software package opened with 7-Zip.",
    },
    ".tar": {
        "name": "Tape archive",
        "icon": "TAR",
        "description": (
            "TAR bundles files and folders into one archive but does not necessarily "
            "compress them. It is common in Linux, development, server backups, and "
            "source-code distributions. TAR is often combined with GZ compression, "
            "creating filenames ending in tar.gz."
        ),
        "example": "A Linux source-code package or server backup archive.",
    },
    ".gz": {
        "name": "Gzip-compressed file",
        "icon": "GZ",
        "description": (
            "GZ indicates data compressed with gzip. It commonly compresses one file "
            "or a TAR archive and is widely used for Linux packages, server logs, "
            "backups, and downloaded datasets. A tar.gz package needs both archive and "
            "decompression handling to open."
        ),
        "example": "A compressed server log or Linux package named project.tar.gz.",
    },
    ".iso": {
        "name": "Disc image",
        "icon": "ISO",
        "description": (
            "ISO is a complete image of an optical disc or installation medium. It may "
            "contain an operating-system installer, recovery tools, software, games, or "
            "archived disc contents. ISO files can be mounted as virtual drives in "
            "Windows and are often several gigabytes."
        ),
        "example": "A Windows installation image or archived software installation disc.",
    },
    ".exe": {
        "name": "Windows executable program",
        "icon": "EXE",
        "description": (
            "EXE files contain programs or installers that Windows can run. They may be "
            "application launchers, setup packages, update tools, or system utilities. "
            "Because deleting an EXE can break installed software or remove an installer "
            "needed for repair, these files stay review-only."
        ),
        "example": "An application installer named setup.exe or a program launcher.",
    },
    ".msi": {
        "name": "Windows Installer package",
        "icon": "MSI",
        "description": (
            "MSI is a Windows Installer package used to install, update, repair, or "
            "remove desktop software. Some applications keep MSI packages for future "
            "maintenance. Deleting the wrong package may prevent repair or uninstallation, "
            "so the toolkit keeps MSI files review-only."
        ),
        "example": "A Zoom, Java, or workplace application installation package.",
    },
    ".msix": {
        "name": "Modern Windows application package",
        "icon": "MSIX",
        "description": (
            "MSIX is Microsoft's modern package format for securely installing and "
            "updating Windows applications. It can include application files, identity "
            "information, permissions, and deployment instructions. Removing an MSIX "
            "file from an application-managed location could affect installation or "
            "repair, so it remains review-only."
        ),
        "example": "A packaged Windows desktop application downloaded for managed installation.",
    },
    ".appx": {
        "name": "Windows app package",
        "icon": "APPX",
        "description": (
            "APPX is a Windows application package used especially by Microsoft Store "
            "and Universal Windows Platform applications. It contains program files and "
            "deployment metadata required for installation. Deleting application-managed "
            "APPX data can damage installation or updates, so it remains review-only."
        ),
        "example": "A Microsoft Store application package prepared for Windows installation.",
    },
}


def extension_help(extension: str) -> dict[str, Any]:
    """Return safe display data for a preset or explicitly custom extension."""

    normalized = extension.casefold()
    help_item = EXTENSION_HELP.get(normalized)
    if help_item is not None:
        return {"extension": normalized, **help_item}
    abbreviation = normalized.removeprefix(".")[:5].upper() or "FILE"
    return {
        "extension": normalized,
        "name": "Custom indexed extension",
        "icon": abbreviation,
        "description": (
            "This extension was explicitly added while creating the local index. "
            "The toolkit does not assume which application owns it or whether it is "
            "safe to remove. Custom file types remain review-only and should be "
            "identified using their originating application."
        ),
        "example": "A vendor-specific file created by one installed application.",
    }
