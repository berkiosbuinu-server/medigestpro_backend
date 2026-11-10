# app/gui/cost_module.py
import tkinter as tk
from tkinter import ttk, messagebox
import ttkbootstrap as ttk
from ttkbootstrap.constants import *
from datetime import datetime, date
from uuid import UUID
import logging

from app.services.cost import CostService

class CostModuleGUI:
    def __init__(self, parent, app):
        self.parent = parent
        self.app = app
        self.db = app.db
        self.tenant_id = app.tenant_id
        self.current_user = app.current_user
        
        self.setup_ui()
        self.load_data()
    
    def setup_ui(self):
        """Configure l'interface utilisateur"""
        # Cadre principal
        self.main_frame = ttk.Frame(self.parent)
        self.main_frame.pack(fill='both', expand=True, padx=10, pady=10)
        
        # Titre
        title_label = ttk.Label(
            self.main_frame,
            text="GESTION DES COÛTS",
            font=('Helvetica', 16, 'bold'),
            bootstyle=PRIMARY
        )
        title_label.pack(pady=(0, 20))
        
        # Notebook pour les différentes sections
        self.notebook = ttk.Notebook(self.main_frame)
        self.notebook.pack(fill='both', expand=True)
        
        # Onglet 1: Saisie des coûts
        self.setup_cost_entry_tab()
        
        # Onglet 2: Liste des coûts
        self.setup_cost_list_tab()
        
        # Onglet 3: Budgets
        self.setup_budgets_tab()
        
        # Onglet 4: Fournisseurs
        self.setup_suppliers_tab()
        
        # Onglet 5: Rapports
        self.setup_reports_tab()
    
    def setup_cost_entry_tab(self):
        """Configure l'onglet de saisie des coûts"""
        entry_frame = ttk.Frame(self.notebook)
        self.notebook.add(entry_frame, text="Nouveau Coût")
        
        # Formulaire
        form_frame = ttk.Labelframe(entry_frame, text="Nouveau Coût", padding=10)
        form_frame.pack(fill='x', padx=10, pady=10)
        
        # Catégorie
        ttk.Label(form_frame, text="Catégorie:").grid(row=0, column=0, sticky='e', padx=5, pady=5)
        self.category_combo = ttk.Combobox(form_frame, values=[
            "Salaire", "Loyer", "Électricité", "Eau", "Téléphone", "Internet",
            "Maintenance", "Fournitures", "Marketing", "Logiciel", "Assurance",
            "Transport", "Formation", "Consultation", "Taxes", "Autre"
        ], state='readonly', width=30)
        self.category_combo.grid(row=0, column=1, padx=5, pady=5)
        self.category_combo.current(0)
        
        # Sous-catégorie
        ttk.Label(form_frame, text="Sous-catégorie:").grid(row=1, column=0, sticky='e', padx=5, pady=5)
        self.subcategory_entry = ttk.Entry(form_frame, width=32)
        self.subcategory_entry.grid(row=1, column=1, padx=5, pady=5)
        
        # Montant
        ttk.Label(form_frame, text="Montant:").grid(row=2, column=0, sticky='e', padx=5, pady=5)
        self.amount_var = tk.DoubleVar(value=0.0)
        self.amount_entry = ttk.Entry(form_frame, textvariable=self.amount_var, width=32)
        self.amount_entry.grid(row=2, column=1, padx=5, pady=5)
        
        # Taxes
        ttk.Label(form_frame, text="Taxes:").grid(row=3, column=0, sticky='e', padx=5, pady=5)
        self.tax_var = tk.DoubleVar(value=0.0)
        self.tax_entry = ttk.Entry(form_frame, textvariable=self.tax_var, width=32)
        self.tax_entry.grid(row=3, column=1, padx=5, pady=5)
        
        # Description
        ttk.Label(form_frame, text="Description:").grid(row=4, column=0, sticky='e', padx=5, pady=5)
        self.description_text = tk.Text(form_frame, height=3, width=30)
        self.description_text.grid(row=4, column=1, padx=5, pady=5)
        
        # Date de paiement
        ttk.Label(form_frame, text="Date paiement:").grid(row=5, column=0, sticky='e', padx=5, pady=5)
        self.payment_date_entry = ttk.DateEntry(form_frame, width=29)
        self.payment_date_entry.grid(row=5, column=1, padx=5, pady=5)
        
        # Méthode de paiement
        ttk.Label(form_frame, text="Méthode:").grid(row=6, column=0, sticky='e', padx=5, pady=5)
        self.payment_method_combo = ttk.Combobox(form_frame, values=[
            "Espèces", "Virement", "Mobile Money", "Chèque", "Carte"
        ], state='readonly', width=29)
        self.payment_method_combo.grid(row=6, column=1, padx=5, pady=5)
        self.payment_method_combo.current(0)
        
        # Fournisseur
        ttk.Label(form_frame, text="Fournisseur:").grid(row=7, column=0, sticky='e', padx=5, pady=5)
        self.supplier_combo = ttk.Combobox(form_frame, width=29)
        self.supplier_combo.grid(row=7, column=1, padx=5, pady=5)
        
        # Numéro de facture
        ttk.Label(form_frame, text="N° Facture:").grid(row=8, column=0, sticky='e', padx=5, pady=5)
        self.invoice_entry = ttk.Entry(form_frame, width=32)
        self.invoice_entry.grid(row=8, column=1, padx=5, pady=5)
        
        # Boutons
        button_frame = ttk.Frame(form_frame)
        button_frame.grid(row=9, column=0, columnspan=2, pady=20)
        
        ttk.Button(
            button_frame,
            text="Enregistrer",
            command=self.save_cost,
            bootstyle=SUCCESS,
            width=15
        ).pack(side='left', padx=5)
        
        ttk.Button(
            button_frame,
            text="Réinitialiser",
            command=self.reset_form,
            bootstyle=WARNING,
            width=15
        ).pack(side='left', padx=5)
    
    def save_cost(self):
        """Enregistre un nouveau coût"""
        try:
            # Récupérer les données du formulaire
            category = self.category_combo.get()
            subcategory = self.subcategory_entry.get() or None
            amount = self.amount_var.get()
            tax = self.tax_var.get()
            description = self.description_text.get("1.0", "end-1c").strip()
            payment_date = self.payment_date_entry.entry.get()
            payment_method = self.payment_method_combo.get()
            supplier = self.supplier_combo.get()
            invoice_number = self.invoice_entry.get() or None
            
            if not category or amount <= 0:
                messagebox.showerror("Erreur", "Catégorie et montant requis")
                return
            
            # Convertir la date
            payment_date = datetime.strptime(payment_date, "%Y-%m-%d").date()
            
            # Récupérer l'ID du fournisseur
            supplier_id = None
            if supplier:
                # Rechercher ou créer le fournisseur
                pass  # À implémenter
            
            # Créer le coût via l'API
            cost_data = {
                "category": category.lower().replace("é", "e").replace("ç", "c"),
                "subcategory": subcategory,
                "amount": amount,
                "tax_amount": tax,
                "description": description,
                "payment_date": payment_date.isoformat(),
                "payment_method": payment_method.lower().replace(" ", "_"),
                "invoice_number": invoice_number,
                "supplier_id": supplier_id,
                "is_paid": True
            }
            
            # Ici, appeler l'API pour créer le coût
            # response = self.app.api_client.post("/costs", json=cost_data)
            
            messagebox.showinfo("Succès", "Coût enregistré avec succès")
            self.reset_form()
            self.load_data()
            
        except Exception as e:
            logging.error(f"Erreur lors de l'enregistrement du coût: {str(e)}")
            messagebox.showerror("Erreur", f"Erreur lors de l'enregistrement: {str(e)}")
    
    def reset_form(self):
        """Réinitialise le formulaire"""
        self.category_combo.current(0)
        self.subcategory_entry.delete(0, tk.END)
        self.amount_var.set(0.0)
        self.tax_var.set(0.0)
        self.description_text.delete("1.0", tk.END)
        self.payment_date_entry.entry.delete(0, tk.END)
        self.payment_date_entry.entry.insert(0, date.today().strftime("%Y-%m-%d"))
        self.payment_method_combo.current(0)
        self.supplier_combo.set('')
        self.invoice_entry.delete(0, tk.END)
    
    def setup_cost_list_tab(self):
        """Configure l'onglet de liste des coûts"""
        list_frame = ttk.Frame(self.notebook)
        self.notebook.add(list_frame, text="Liste des Coûts")
        
        # Filtres
        filter_frame = ttk.Frame(list_frame)
        filter_frame.pack(fill='x', padx=10, pady=10)
        
        ttk.Label(filter_frame, text="Période:").pack(side='left', padx=5)
        self.period_combo = ttk.Combobox(filter_frame, values=[
            "Aujourd'hui", "Cette semaine", "Ce mois", "Ce trimestre", "Cette année", "Tous"
        ], state='readonly', width=15)
        self.period_combo.pack(side='left', padx=5)
        self.period_combo.current(2)
        
        ttk.Label(filter_frame, text="Catégorie:").pack(side='left', padx=5)
        self.filter_category_combo = ttk.Combobox(filter_frame, values=[
            "Toutes", "Salaire", "Loyer", "Électricité", "Maintenance", "Fournitures", "Autre"
        ], state='readonly', width=15)
        self.filter_category_combo.pack(side='left', padx=5)
        self.filter_category_combo.current(0)
        
        ttk.Button(
            filter_frame,
            text="Filtrer",
            command=self.filter_costs,
            bootstyle=PRIMARY,
            width=10
        ).pack(side='left', padx=5)
        
        ttk.Button(
            filter_frame,
            text="Exporter",
            command=self.export_costs,
            bootstyle=INFO,
            width=10
        ).pack(side='left', padx=5)
        
        # Treeview pour afficher les coûts
        columns = ("Date", "Catégorie", "Montant", "Description", "Fournisseur", "Facture")
        self.costs_tree = ttk.Treeview(
            list_frame,
            columns=columns,
            show="headings",
            height=20,
            bootstyle=PRIMARY
        )
        
        # Configurer les colonnes
        for col in columns:
            self.costs_tree.heading(col, text=col)
            if col == "Montant":
                self.costs_tree.column(col, width=100, anchor='e')
            else:
                self.costs_tree.column(col, width=150)
        
        # Scrollbar
        scrollbar = ttk.Scrollbar(list_frame, orient='vertical', command=self.costs_tree.yview)
        self.costs_tree.configure(yscrollcommand=scrollbar.set)
        
        # Pack
        self.costs_tree.pack(side='left', fill='both', expand=True, padx=(10, 0), pady=10)
        scrollbar.pack(side='right', fill='y', padx=(0, 10), pady=10)
        
        # Menu contextuel
        self.setup_context_menu()
    
    def setup_context_menu(self):
        """Configure le menu contextuel pour la treeview"""
        self.context_menu = tk.Menu(self.costs_tree, tearoff=0)
        self.context_menu.add_command(label="Voir détails", command=self.view_cost_details)
        self.context_menu.add_command(label="Modifier", command=self.edit_cost)
        self.context_menu.add_command(label="Supprimer", command=self.delete_cost)
        self.context_menu.add_separator()
        self.context_menu.add_command(label="Marquer comme payé", command=self.mark_as_paid)
        
        self.costs_tree.bind("<Button-3>", self.show_context_menu)
    
    def show_context_menu(self, event):
        """Affiche le menu contextuel"""
        item = self.costs_tree.identify_row(event.y)
        if item:
            self.costs_tree.selection_set(item)
            self.context_menu.post(event.x_root, event.y_root)
    
    def view_cost_details(self):
        """Affiche les détails d'un coût"""
        selected_item = self.costs_tree.selection()
        if not selected_item:
            messagebox.showwarning("Sélection", "Veuillez sélectionner un coût")
            return
        
        # Récupérer les données du coût
        item_data = self.costs_tree.item(selected_item[0], 'values')
        
        # Afficher dans une nouvelle fenêtre
        details_window = tk.Toplevel(self.parent)
        details_window.title("Détails du Coût")
        details_window.geometry("400x300")
        
        # Afficher les détails
        ttk.Label(details_window, text="Détails du Coût", font=('Helvetica', 14, 'bold')).pack(pady=10)
        
        details_text = f"""
        Date: {item_data[0]}
        Catégorie: {item_data[1]}
        Montant: {item_data[2]}
        Description: {item_data[3]}
        Fournisseur: {item_data[4]}
        Facture: {item_data[5]}
        """
        
        ttk.Label(details_window, text=details_text, justify='left').pack(padx=20, pady=20)
    
    def edit_cost(self):
        """Modifie un coût existant"""
        # À implémenter
        pass
    
    def delete_cost(self):
        """Supprime un coût"""
        selected_item = self.costs_tree.selection()
        if not selected_item:
            messagebox.showwarning("Sélection", "Veuillez sélectionner un coût")
            return
        
        if messagebox.askyesno("Confirmation", "Voulez-vous vraiment supprimer ce coût?"):
            # Supprimer via l'API
            # À implémenter
            self.load_data()
    
    def mark_as_paid(self):
        """Marque un coût comme payé"""
        selected_item = self.costs_tree.selection()
        if not selected_item:
            messagebox.showwarning("Sélection", "Veuillez sélectionner un coût")
            return
        
        # Mettre à jour via l'API
        # À implémenter
        self.load_data()
    
    def filter_costs(self):
        """Filtre la liste des coûts"""
        # À implémenter
        pass
    
    def export_costs(self):
        """Exporte les coûts"""
        # À implémenter
        pass
    
    def setup_budgets_tab(self):
        """Configure l'onglet des budgets"""
        budgets_frame = ttk.Frame(self.notebook)
        self.notebook.add(budgets_frame, text="Budgets")
        
        # Cadre pour créer un nouveau budget
        create_frame = ttk.Labelframe(budgets_frame, text="Nouveau Budget", padding=10)
        create_frame.pack(fill='x', padx=10, pady=(10, 5))
        
        # Formulaire de création de budget
        ttk.Label(create_frame, text="Nom:").grid(row=0, column=0, sticky='e', padx=5, pady=5)
        self.budget_name_entry = ttk.Entry(create_frame, width=30)
        self.budget_name_entry.grid(row=0, column=1, padx=5, pady=5)
        
        ttk.Label(create_frame, text="Catégorie:").grid(row=1, column=0, sticky='e', padx=5, pady=5)
        self.budget_category_combo = ttk.Combobox(create_frame, values=[
            "Salaire", "Loyer", "Électricité", "Maintenance", "Fournitures", "Marketing", "Autre"
        ], state='readonly', width=28)
        self.budget_category_combo.grid(row=1, column=1, padx=5, pady=5)
        self.budget_category_combo.current(0)
        
        ttk.Label(create_frame, text="Période:").grid(row=2, column=0, sticky='e', padx=5, pady=5)
        self.budget_period_combo = ttk.Combobox(create_frame, values=["Mensuel", "Trimestriel", "Annuel"], state='readonly', width=28)
        self.budget_period_combo.grid(row=2, column=1, padx=5, pady=5)
        self.budget_period_combo.current(0)
        
        ttk.Label(create_frame, text="Montant:").grid(row=3, column=0, sticky='e', padx=5, pady=5)
        self.budget_amount_var = tk.DoubleVar(value=0.0)
        self.budget_amount_entry = ttk.Entry(create_frame, textvariable=self.budget_amount_var, width=30)
        self.budget_amount_entry.grid(row=3, column=1, padx=5, pady=5)
        
        ttk.Button(
            create_frame,
            text="Créer Budget",
            command=self.create_budget,
            bootstyle=SUCCESS
        ).grid(row=4, column=1, pady=10, sticky='e')
        
        # Liste des budgets
        list_frame = ttk.Labelframe(budgets_frame, text="Budgets Actifs", padding=10)
        list_frame.pack(fill='both', expand=True, padx=10, pady=5)
        
        columns = ("Nom", "Catégorie", "Période", "Alloué", "Dépensé", "Reste", "%")
        self.budgets_tree = ttk.Treeview(list_frame, columns=columns, show="headings", height=10)
        
        for col in columns:
            self.budgets_tree.heading(col, text=col)
            if col in ("Alloué", "Dépensé", "Reste"):
                self.budgets_tree.column(col, width=100, anchor='e')
            else:
                self.budgets_tree.column(col, width=100)
        
        scrollbar = ttk.Scrollbar(list_frame, orient='vertical', command=self.budgets_tree.yview)
        self.budgets_tree.configure(yscrollcommand=scrollbar.set)
        
        self.budgets_tree.pack(side='left', fill='both', expand=True)
        scrollbar.pack(side='right', fill='y')
    
    def create_budget(self):
        """Crée un nouveau budget"""
        # À implémenter
        pass
    
    def setup_suppliers_tab(self):
        """Configure l'onglet des fournisseurs"""
        suppliers_frame = ttk.Frame(self.notebook)
        self.notebook.add(suppliers_frame, text="Fournisseurs")
        
        # Cadre pour ajouter un fournisseur
        add_frame = ttk.Labelframe(suppliers_frame, text="Nouveau Fournisseur", padding=10)
        add_frame.pack(fill='x', padx=10, pady=(10, 5))
        
        # Formulaire
        ttk.Label(add_frame, text="Nom:").grid(row=0, column=0, sticky='e', padx=5, pady=5)
        self.supplier_name_entry = ttk.Entry(add_frame, width=30)
        self.supplier_name_entry.grid(row=0, column=1, padx=5, pady=5)
        
        ttk.Label(add_frame, text="Entreprise:").grid(row=1, column=0, sticky='e', padx=5, pady=5)
        self.supplier_company_entry = ttk.Entry(add_frame, width=30)
        self.supplier_company_entry.grid(row=1, column=1, padx=5, pady=5)
        
        ttk.Label(add_frame, text="Téléphone:").grid(row=2, column=0, sticky='e', padx=5, pady=5)
        self.supplier_phone_entry = ttk.Entry(add_frame, width=30)
        self.supplier_phone_entry.grid(row=2, column=1, padx=5, pady=5)
        
        ttk.Label(add_frame, text="Email:").grid(row=3, column=0, sticky='e', padx=5, pady=5)
        self.supplier_email_entry = ttk.Entry(add_frame, width=30)
        self.supplier_email_entry.grid(row=3, column=1, padx=5, pady=5)
        
        ttk.Button(
            add_frame,
            text="Ajouter Fournisseur",
            command=self.add_supplier,
            bootstyle=SUCCESS
        ).grid(row=4, column=1, pady=10, sticky='e')
        
        # Liste des fournisseurs
        list_frame = ttk.Labelframe(suppliers_frame, text="Liste des Fournisseurs", padding=10)
        list_frame.pack(fill='both', expand=True, padx=10, pady=5)
        
        columns = ("Nom", "Entreprise", "Téléphone", "Email", "Total Achats")
        self.suppliers_tree = ttk.Treeview(list_frame, columns=columns, show="headings", height=15)
        
        for col in columns:
            self.suppliers_tree.heading(col, text=col)
            self.suppliers_tree.column(col, width=120)
        
        scrollbar = ttk.Scrollbar(list_frame, orient='vertical', command=self.suppliers_tree.yview)
        self.suppliers_tree.configure(yscrollcommand=scrollbar.set)
        
        self.suppliers_tree.pack(side='left', fill='both', expand=True)
        scrollbar.pack(side='right', fill='y')
    
    def add_supplier(self):
        """Ajoute un nouveau fournisseur"""
        # À implémenter
        pass
    
    def setup_reports_tab(self):
        """Configure l'onglet des rapports"""
        reports_frame = ttk.Frame(self.notebook)
        self.notebook.add(reports_frame, text="Rapports")
        
        # Options de rapport
        options_frame = ttk.Frame(reports_frame)
        options_frame.pack(fill='x', padx=10, pady=10)
        
        ttk.Label(options_frame, text="Type de rapport:").pack(side='left', padx=5)
        self.report_type_combo = ttk.Combobox(options_frame, values=[
            "Mensuel", "Trimestriel", "Annuel", "Par catégorie", "Par fournisseur", "Analyse des budgets"
        ], state='readonly', width=20)
        self.report_type_combo.pack(side='left', padx=5)
        self.report_type_combo.current(0)
        
        ttk.Label(options_frame, text="Période:").pack(side='left', padx=5)
        self.report_period_combo = ttk.Combobox(options_frame, values=[
            "Ce mois", "Dernier mois", "Ce trimestre", "Dernier trimestre", "Cette année", "Dernière année"
        ], state='readonly', width=15)
        self.report_period_combo.pack(side='left', padx=5)
        self.report_period_combo.current(0)
        
        ttk.Button(
            options_frame,
            text="Générer",
            command=self.generate_report,
            bootstyle=PRIMARY,
            width=10
        ).pack(side='left', padx=5)
        
        ttk.Button(
            options_frame,
            text="Exporter",
            command=self.export_report,
            bootstyle=INFO,
            width=10
        ).pack(side='left', padx=5)
        
        # Zone d'affichage du rapport
        report_frame = ttk.Frame(reports_frame)
        report_frame.pack(fill='both', expand=True, padx=10, pady=(0, 10))
        
        # Zone de texte pour afficher le rapport
        self.report_text = tk.Text(report_frame, height=20, width=80)
        scrollbar = ttk.Scrollbar(report_frame, orient='vertical', command=self.report_text.yview)
        self.report_text.configure(yscrollcommand=scrollbar.set)
        
        self.report_text.pack(side='left', fill='both', expand=True)
        scrollbar.pack(side='right', fill='y')
    
    def generate_report(self):
        """Génère un rapport"""
        report_type = self.report_type_combo.get()
        period = self.report_period_combo.get()
        
        # Générer le rapport via le service
        cost_service = CostService(self.db, self.tenant_id)
        
        if report_type == "Mensuel":
            today = date.today()
            report = cost_service.generate_monthly_report(today.year, today.month)
            
            # Formater et afficher le rapport
            report_str = f"""
            RAPPORT MENSUEL DES COÛTS
            Période: {report['period']}
            
            Total des coûts: {report['total_costs']:,.2f} CDF
            Nombre de transactions: {report['total_transactions']}
            
            Distribution par catégorie:
            """
            
            for category, data in report['by_category'].items():
                percentage = (data['amount'] / report['total_costs'] * 100) if report['total_costs'] > 0 else 0
                report_str += f"\n  {category}: {data['amount']:,.2f} CDF ({data['count']} transactions, {percentage:.1f}%)"
            
            self.report_text.delete("1.0", tk.END)
            self.report_text.insert("1.0", report_str)
        
        # À compléter pour les autres types de rapports
    
    def export_report(self):
        """Exporte le rapport"""
        # À implémenter
        pass
    
    def load_data(self):
        """Charge les données initiales"""
        self.load_costs()
        self.load_suppliers()
        self.load_budgets()
    
    def load_costs(self):
        """Charge la liste des coûts"""
        # Vider la treeview
        for item in self.costs_tree.get_children():
            self.costs_tree.delete(item)
        
        # Récupérer les coûts via l'API
        # À implémenter
        # Pour l'instant, données fictives
        sample_data = [
            ("2024-01-15", "Loyer", "500,000 CDF", "Loyer janvier", "Propriétaire", "FAC-001"),
            ("2024-01-20", "Électricité", "150,000 CDF", "Facture Snel", "SNEL", "SNEL-456"),
            ("2024-01-25", "Salaire", "2,500,000 CDF", "Salaires janvier", "-", "-"),
        ]
        
        for data in sample_data:
            self.costs_tree.insert("", "end", values=data)
    
    def load_suppliers(self):
        """Charge la liste des fournisseurs"""
        # À implémenter
        pass
    
    def load_budgets(self):
        """Charge la liste des budgets"""
        # À implémenter
        pass