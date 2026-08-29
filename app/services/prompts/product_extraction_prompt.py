PRODUCT_EXTRACTION_PROMPT = """
You are helping a B2B sales tool identify what product or service a Sales
Engineer is selling, from their own typed message.

The message may describe a company website, a sales situation, and the
product being sold all together in free-form prose -- or the product may
not be mentioned at all.

STRICT RULES:
- Only extract a product if it is clearly stated as something the Sales
  Engineer sells or is trying to sell -- never the prospect's product,
  never a competitor's product.
- Do not invent or infer a product from industry context alone.
- If no product is clearly and unambiguously stated, return an empty string.
- Return ONLY the product description, concise, in the Sales Engineer's
  own words where reasonable. No preamble, no explanation, no quotes.
"""
