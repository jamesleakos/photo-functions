#!/usr/bin/env python3

import os
import shutil
from pathlib import Path
from tqdm import tqdm

def get_image_files(folder):
    """Recursively get all image files in a folder."""
    image_extensions = {'.jpg', '.jpeg', '.png', '.heic'}
    files = []
    for path in Path(folder).rglob('*'):
        if path.suffix.lower() in image_extensions:
            files.append(path)
    return files

def merge_photos(source_folder, target_folder):
    """Merge photos from source folder into target folder if they don't exist."""
    if not os.path.exists(source_folder) or not os.path.exists(target_folder):
        raise ValueError("Both source and target folders must exist")

    # Create backup folder
    backup_folder = os.path.join(target_folder, '_merged_photos_backup')
    os.makedirs(backup_folder, exist_ok=True)

    # Get all image files
    print("Scanning folders...")
    source_files = get_image_files(source_folder)
    target_files = get_image_files(target_folder)

    # Get set of filenames in target
    target_filenames = {file.name.lower() for file in target_files}

    # Process source files
    print("Processing source photos...")
    copied_count = 0
    for source_file in tqdm(source_files):
        # If photo doesn't exist in target (by name), copy it
        if source_file.name.lower() not in target_filenames:
            # Create the same relative path structure in target
            rel_path = os.path.relpath(source_file, source_folder)
            target_path = os.path.join(target_folder, rel_path)
            
            # Create necessary directories
            os.makedirs(os.path.dirname(target_path), exist_ok=True)
            
            # Copy the file
            shutil.copy2(source_file, target_path)
            copied_count += 1

    print(f"\nMerge complete! {copied_count} photos were added to the target folder.")

if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description="Merge photos from source folder into target folder")
    parser.add_argument("source_folder", help="Folder containing photos to be merged")
    parser.add_argument("target_folder", help="Folder to merge photos into")
    args = parser.parse_args()

    merge_photos(args.source_folder, args.target_folder) 