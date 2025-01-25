# Photo Library Manager

A collection of Python tools for managing your photo library.

## Installation

```bash
pip install -r requirements.txt
```

You'll also need ExifTool installed:

- macOS: `brew install exiftool`
- Linux: `sudo apt-get install exiftool`
- Windows: Download from https://exiftool.org

## Tools

### MOV Cleaner

Deletes .MOV files that have matching .HEIC files (starting with IMG\_).

```bash
python src/mov_cleaner.py [directory]
```

```bash
python src/mov_cleaner.py photos/phone
```

### HEIC to JPG Converter

Converts HEIC files to JPG format.

```bash
python src/heic_to_jpg.py [folder_path] [--delete-original]
```

```bash
python src/heic_to_jpg.py photos/phone --delete-original
```

### Photo Replacer

Replaces lower resolution phone photos with matching camera photos.

```bash
python src/photo_replacer.py [phone_folder] [camera_folder]
```

```bash
python src/photo_replacer.py photos/phone photos/camera
```

### Photo Merger

Merges photos from source to target folder, avoiding duplicates.

```bash
python src/photo_merger.py [source_folder] [target_folder]
```

```bash
python src/photo_merger.py photos/camera photos/phone
```

### Date Fixer

Sets the file creation date to match the content creation date from metadata. Works with most file types that contain creation date metadata (photos, videos, documents, etc.).

```bash
python src/fix_dates.py [folder_path]
```

```bash
python src/fix_dates.py photos/phone
```

## Features

- Recursive folder processing
- Backup creation for safety
