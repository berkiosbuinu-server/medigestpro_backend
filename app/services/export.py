# app/services/export.py
import csv
import io
from datetime import datetime
from enum import Enum
import logging
from pathlib import Path

import pandas as pd

logger = logging.getLogger(__name__)

class ExportFormat(str, Enum):
    EXCEL = "excel"
    CSV = "csv"

class ExportService:
    """
    Service pour exporter le stock ou d'autres données.
    Peut générer des fichiers CSV ou Excel.
    """

    def __init__(self, tenant):
        self.tenant = tenant
        self.output_dir = Path(f"exports/{self.tenant.id}")
        self.output_dir.mkdir(parents=True, exist_ok=True)

    def export_stock(self, data: list, export_format: ExportFormat = ExportFormat.EXCEL, user_id: str = None):
        """
        Exporte la liste des produits.
        :param data: liste de dictionnaires représentant les produits
        :param export_format: format d'export (EXCEL ou CSV)
        :param user_id: identifiant de l'utilisateur qui lance l'export
        :return: chemin du fichier généré
        """
        timestamp = datetime.utcnow().strftime("%Y%m%d_%H%M%S")
        filename = f"stock_{timestamp}_{user_id or 'anon'}"

        if export_format == ExportFormat.EXCEL:
            file_path = self.output_dir / f"{filename}.xlsx"
            try:
                df = pd.DataFrame(data)
                df.to_excel(file_path, index=False)
                logger.info(f"Export Excel créé : {file_path}")
            except Exception as e:
                logger.error(f"Erreur lors de l'export Excel : {e}")
                raise
        elif export_format == ExportFormat.CSV:
            file_path = self.output_dir / f"{filename}.csv"
            try:
                keys = data[0].keys() if data else []
                with open(file_path, mode="w", newline="", encoding="utf-8") as f:
                    writer = csv.DictWriter(f, fieldnames=keys)
                    writer.writeheader()
                    writer.writerows(data)
                logger.info(f"Export CSV créé : {file_path}")
            except Exception as e:
                logger.error(f"Erreur lors de l'export CSV : {e}")
                raise
        else:
            raise ValueError(f"Format d'export non supporté : {export_format}")

        return file_path

    def export_to_bytes(self, data: list, export_format: ExportFormat = ExportFormat.EXCEL) -> bytes:
        """
        Retourne l'export en mémoire sous forme de bytes (utile pour API direct)
        """
        if export_format == ExportFormat.EXCEL:
            output = io.BytesIO()
            df = pd.DataFrame(data)
            df.to_excel(output, index=False)
            return output.getvalue()
        elif export_format == ExportFormat.CSV:
            output = io.StringIO()
            keys = data[0].keys() if data else []
            writer = csv.DictWriter(output, fieldnames=keys)
            writer.writeheader()
            writer.writerows(data)
            return output.getvalue().encode("utf-8")
        else:
            raise ValueError(f"Format d'export non supporté : {export_format}")
