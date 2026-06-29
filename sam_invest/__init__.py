"""Sam_Invest - outil personnel d'aide a la decision d'investissement.

Architecture NON NEGOCIABLE :
  - Couche donnees (ce package, hors llm.py) : 100% deterministe, sans LLM.
    Tout chiffre (prix, ratios, indicateurs, valeurs de portefeuille) est
    calcule par du code Python.
  - Couche jugement (llm.py uniquement) : API Claude. Sert seulement a
    resumer/classer les news et a rediger une synthese en langage naturel
    A PARTIR des chiffres fournis par le code. Ne produit jamais un chiffre
    ni un verdict acheter/vendre.
"""

__version__ = "1.0.0"
