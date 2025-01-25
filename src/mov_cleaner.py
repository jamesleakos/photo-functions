import os

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
    parser = argparse.ArgumentParser(description="Delete MOV files that have matching HEIC files")
    parser.add_argument("directory", help="Directory containing the HEIC and MOV files")
    args = parser.parse_args()
    
    mov_deleter(args.directory)

if __name__ == "__main__":
    main() 