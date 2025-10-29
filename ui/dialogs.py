from tkinter import filedialog


class Dialogs:
    @staticmethod
    def pick_files():
        paths = filedialog.askopenfilenames(title="Selectează fișierele de trimis")
        return list(paths)

    @staticmethod
    def pick_folder(initial_dir: str):
        return filedialog.askdirectory(title="Alege folderul de descărcare", initialdir=initial_dir)
