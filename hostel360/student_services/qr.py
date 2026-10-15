"""QR codes as PNG data URIs for print formats.

wkhtmltopdf renders raster images far more reliably than inline SVG, so the QR matrix
from pyqrcode (bundled with Frappe) is drawn into a PNG with Pillow.
"""

import base64
from io import BytesIO


def qr_png_data_uri(value: str, module_size: int = 6, quiet_zone: int = 4) -> str:
	import pyqrcode
	from PIL import Image

	matrix = pyqrcode.create(value, error="M").code
	size = len(matrix) + 2 * quiet_zone
	image = Image.new("1", (size, size), 1)
	pixels = image.load()
	for y, row in enumerate(matrix):
		for x, dark in enumerate(row):
			if dark:
				pixels[x + quiet_zone, y + quiet_zone] = 0
	image = image.resize((size * module_size, size * module_size), Image.Resampling.NEAREST)

	buffer = BytesIO()
	image.save(buffer, format="PNG", optimize=True)
	return "data:image/png;base64," + base64.b64encode(buffer.getvalue()).decode()
