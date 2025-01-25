import os
import shutil
from pathlib import Path
from tqdm import tqdm

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
                if Path(file).suffix.lower() in {'.jpg', '.jpeg'}:
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

    def replace_photos(self):
        """Replace lower resolution phone photos with their camera counterparts."""
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
        for phone_img, camera_img in tqdm(matches, desc="Replacing photos"):
            try:
                # Create backup
                backup_path = backup_folder / phone_img.name
                shutil.copy2(phone_img, backup_path)
                
                # Replace with camera version
                shutil.copy2(camera_img, phone_img)
                replaced_count += 1
                
            except Exception as e:
                print(f"Error replacing {phone_img.name}: {str(e)}")
        
        print(f"\nReplacement complete! {replaced_count} photos were replaced.")

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