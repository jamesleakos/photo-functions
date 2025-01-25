# Photo Library Manager

A collection of Python tools for managing my photo library.

## Installation

```bash
pip install -r requirements.txt
```

## Tools

### Photo Replacer

Replaces lower resolution phone photos with matching camera photos.

```bash
python src/photo_replacer.py [phone_folder] [camera_folder]
```

### Photo Merger

Merges photos from source to target folder, avoiding duplicates.

```bash
python src/photo_merger.py [source_folder] [target_folder]
```

### HEIC to JPG Converter

Converts HEIC files to JPG format.

```bash
python src/heic_to_jpg.py [folder_path] [--delete-original]
```

### MOV Cleaner

Deletes .MOV files that have matching .HEIC files (starting with IMG\_).

```bash
python src/mov_cleaner.py [directory]
```

## Features

- Recursive folder processing
- Backup creation for safety
