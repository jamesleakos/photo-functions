#!/usr/bin/env python3

import os
import shutil
from pathlib import Path
from tqdm import tqdm
import subprocess

class PhotoReplacer:
    def __init__(self, phone_folder: str, camera_folder: str):
        """
        Initialize the PhotoReplacer with paths to phone and camera folders.
        
        Args:
            phone_folder (str): Path to the folder containing phone photos
            camera_folder (str): Path to the folder containing camera photos
        """
        self.phone_folder = Path(phone_folder)
        self.camera_folder = Path(camera_folder)
        
        if not self.phone_folder.exists():
            raise ValueError(f"Phone folder does not exist: {phone_folder}")
        if not self.camera_folder.exists():
            raise ValueError(f"Camera folder does not exist: {camera_folder}")

    def get_image_files(self, folder: Path) -> list:
        """Get all image files in a folder and its subfolders."""
        image_files = []
        
        for root, _, files in os.walk(folder):
            for file in files:
                if Path(file).suffix.lower() in {'.jpg', '.jpeg', '.heic'}:
                    image_files.append(Path(root) / file)
        
        return image_files

    def find_matching_photos(self) -> list:
        """Find matching photos between phone and camera folders by filename."""
        print("Scanning folders for images...")
        phone_images = self.get_image_files(self.phone_folder)
        camera_images = self.get_image_files(self.camera_folder)
        
        matches = []
        print("Finding matching photos...")
        
        # Create a dictionary of camera images by filename
        camera_dict = {img.name.lower(): img for img in camera_images}
        
        # Find matches in phone images
        for phone_img in tqdm(phone_images, desc="Processing phone images"):
            if phone_img.name.lower() in camera_dict:
                matches.append((phone_img, camera_dict[phone_img.name.lower()]))
        
        return matches

    def copy_file_with_metadata(self, source_path, target_path, original_path=None):
        """
        Copy file and preserve all metadata.
        If original_path is provided, preserve its metadata instead of source_path's metadata.
        """
        try:
            # First, copy the file with shutil to handle the data
            shutil.copy2(source_path, target_path)
            
            # Then use exiftool to copy all metadata
            # If original_path is provided, use its metadata
            metadata_source = str(original_path if original_path else source_path)
            
            subprocess.run(
                ['exiftool', '-TagsFromFile', metadata_source, 
                 '-all:all', '-overwrite_original', str(target_path)],
                check=True, capture_output=True
            )
            
            return True
        except Exception as e:
            print(f"Error copying file and metadata: {str(e)}")
            return False

    def replace_photos(self):
        """Replace lower resolution phone photos with their camera counterparts while preserving metadata."""
        matches = self.find_matching_photos()
        
        if not matches:
            print("No matching photos found.")
            return
        
        print(f"\nFound {len(matches)} matching photos.")
        print("Replacing phone photos with camera versions...")
        
        # Create backup folder
        backup_folder = self.phone_folder / '_replaced_photos_backup'
        backup_folder.mkdir(exist_ok=True)
        
        # Replace photos
        replaced_count = 0
        errors = []
        
        for phone_img, camera_img in tqdm(matches, desc="Replacing photos"):
            try:
                # Create backup path
                backup_path = backup_folder / phone_img.name
                
                # First backup the phone photo with its metadata
                if not self.copy_file_with_metadata(phone_img, backup_path):
                    errors.append((phone_img, "Failed to create backup"))
                    continue
                
                # Replace with camera version while preserving phone photo's metadata
                if self.copy_file_with_metadata(camera_img, phone_img, original_path=phone_img):
                    replaced_count += 1
                else:
                    errors.append((phone_img, "Failed to replace with camera version"))
                
            except Exception as e:
                errors.append((phone_img, str(e)))
                continue
        
        # Print summary
        print(f"\nReplacement complete!")
        print(f"Successfully replaced: {replaced_count} photos")
        if errors:
            print(f"Failed to replace: {len(errors)} files")
            print("\nErrors:")
            for file, error in errors:
                print(f"{file}: {error}")

def mov_deleter(directory_path):
    """
    Deletes .MOV files that have corresponding .HEIC files with the same name,
    but only if the filename starts with 'IMG_'
    
    Args:
        directory_path (str): Path to the directory containing the files
    """
    # Get all HEIC files that start with IMG_
    heic_files = [f for f in os.listdir(directory_path) 
                  if f.upper().endswith('.HEIC') and f.startswith('IMG_')]
    
    # Get base names without extension
    heic_bases = [os.path.splitext(f)[0] for f in heic_files]
    
    # Check for corresponding MOV files
    for base_name in heic_bases:
        mov_path = os.path.join(directory_path, base_name + '.MOV')
        if os.path.exists(mov_path):
            try:
                os.remove(mov_path)
                print(f"Deleted: {mov_path}")
            except Exception as e:
                print(f"Error deleting {mov_path}: {e}")

def main():
    import argparse
    parser = argparse.ArgumentParser(description="Replace phone photos with matching camera photos")
    parser.add_argument("phone_folder", help="Folder containing phone photos")
    parser.add_argument("camera_folder", help="Folder containing camera photos")
    args = parser.parse_args()
    
    replacer = PhotoReplacer(args.phone_folder, args.camera_folder)
    replacer.replace_photos()

if __name__ == "__main__":
    main() 