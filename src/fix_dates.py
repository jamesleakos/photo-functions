#!/usr/bin/env python3

import os
from pathlib import Path
from tqdm import tqdm
from PIL import Image
from PIL.ExifTags import TAGS
import piexif
from datetime import datetime
import subprocess
import json
import platform

def get_creation_date_exiftool(file_path):
    """Get creation date using exiftool."""
    try:
        # Run exiftool and get JSON output with all possible date fields
        result = subprocess.run(
            ['exiftool', '-json', 
             '-ContentCreateDate', '-CreationDate', '-DateTimeOriginal', 
             '-CreateDate', '-FileModifyDate', file_path],
            capture_output=True, text=True
        )
        if result.returncode != 0:
            return None
        
        data = json.loads(result.stdout)[0]
        
        # Try different date fields in order of preference
        for field in ['ContentCreateDate', 'CreationDate', 'DateTimeOriginal', 'CreateDate', 'FileModifyDate']:
            if field in data and data[field]:
                date_str = data[field]
                # Handle various date formats
                try:
                    if '+' in date_str:
                        date_str = date_str.split('+')[0].strip()
                    if '.' in date_str:
                        date_str = date_str.split('.')[0].strip()
                    return datetime.strptime(date_str, '%Y:%m:%d %H:%M:%S')
                except ValueError:
                    continue
        return None
    except Exception as e:
        print(f"ExifTool error: {str(e)}")
        return None

def get_creation_date_pillow(file_path):
    """Get creation date using Pillow/piexif."""
    try:
        with Image.open(file_path) as img:
            exif_dict = piexif.load(img.info.get('exif', b''))
            
            # Try to get the original date from EXIF
            date_original = None
            if '0th' in exif_dict and piexif.ImageIFD.DateTime in exif_dict['0th']:
                date_original = exif_dict['0th'][piexif.ImageIFD.DateTime]
            elif 'Exif' in exif_dict and piexif.ExifIFD.DateTimeOriginal in exif_dict['Exif']:
                date_original = exif_dict['Exif'][piexif.ExifIFD.DateTimeOriginal]
            
            if date_original:
                if isinstance(date_original, bytes):
                    date_original = date_original.decode()
                return datetime.strptime(date_original, '%Y:%m:%d %H:%M:%S')
    except Exception:
        return None
    return None

def set_macos_creation_date(file_path, date_obj):
    """Set the macOS creation date using SetFile command."""
    try:
        # Format date for SetFile command (MM/DD/YYYY HH:MM:SS)
        date_str = date_obj.strftime('%m/%d/%Y %H:%M:%S')
        subprocess.run(['SetFile', '-d', date_str, str(file_path)], check=True)
        return True
    except Exception as e:
        print(f"SetFile error: {str(e)}")
        return False

def fix_file_dates(folder_path):
    """
    Set the file creation date to match the content creation date from metadata.
    Attempts to read metadata from any file type.
    """
    # Check if we're on macOS
    is_macos = platform.system() == 'Darwin'
    if not is_macos:
        print("Warning: This script is optimized for macOS. File creation dates might not be set correctly on other systems.")

    # Get all files
    files = []
    for path in Path(folder_path).rglob('*'):
        if path.is_file():
            files.append(path)

    if not files:
        print("No files found in the specified folder.")
        return

    print(f"Found {len(files)} files. Processing...")
    processed_count = 0
    errors = []

    for file_path in tqdm(files):
        try:
            # Try different methods to get creation date
            date_obj = None
            
            # Try exiftool first (works with most file types)
            date_obj = get_creation_date_exiftool(file_path)
            
            # If exiftool failed and it's an image, try Pillow
            if not date_obj and file_path.suffix.lower() in {'.jpg', '.jpeg', '.heic', '.png', '.tiff', '.bmp'}:
                date_obj = get_creation_date_pillow(file_path)
            
            if date_obj:
                success = True
                # Set macOS creation date if on macOS
                if is_macos:
                    success = set_macos_creation_date(file_path, date_obj)
                
                # Set modification time
                if success:
                    timestamp = date_obj.timestamp()
                    os.utime(file_path, (timestamp, timestamp))
                    processed_count += 1
                else:
                    errors.append((file_path, "Failed to set creation date"))
            else:
                errors.append((file_path, "No creation date found in metadata"))

        except Exception as e:
            errors.append((file_path, str(e)))
            continue

    # Print summary
    print(f"\nProcessing complete!")
    print(f"Successfully processed: {processed_count} files")
    if errors:
        print(f"Failed to process: {len(errors)} files")
        print("\nErrors:")
        for file, error in errors:
            print(f"{file}: {error}")

if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description="Fix file creation dates to match content creation date from metadata")
    parser.add_argument("folder_path", help="Folder containing files to process")
    args = parser.parse_args()

    fix_file_dates(args.folder_path) 