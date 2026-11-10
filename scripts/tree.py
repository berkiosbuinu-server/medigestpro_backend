import os

def list_files(startpath):
    # Dossiers à ignorer
    ignore_dirs = {'.git', '__pycache__', 'venv', 'env', '.idea', '.vscode'}
    
    for root, dirs, files in os.walk(startpath):
        # Filtrer les dossiers ignorés
        dirs[:] = [d for d in dirs if d not in ignore_dirs]
        
        level = root.replace(startpath, '').count(os.sep)
        indent = ' ' * 4 * (level)
        print(f'{indent}{os.path.basename(root)}/')
        subindent = ' ' * 4 * (level + 1)
        for f in files:
            print(f'{subindent}{f}')

if __name__ == '__main__':
    list_files('.')