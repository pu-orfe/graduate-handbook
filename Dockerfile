FROM python:3.11-slim

# LibreOffice renders the PDF; poppler-utils is for the layout tests. The template uses Calibri (body default), Cambria,
# Arial and Times New Roman; Carlito, Caladea and Liberation are their metric-
# compatible stand-ins. Without them body text falls back to DejaVu Sans, which
# is wider and pushes the PDF from 15 pages to 20.
RUN apt-get update && apt-get install -y --no-install-recommends \
    libreoffice-writer \
    poppler-utils \
    default-jre-headless \
    fonts-liberation \
    fonts-crosextra-carlito \
    fonts-crosextra-caladea \
    fonts-dejavu-core \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY handbook_generator ./handbook_generator
COPY tests ./tests
COPY media ./media
COPY template ./template

ENV PYTHONPATH=/app \
    PYTHONUNBUFFERED=1 \
    HOME=/tmp \
    REQUIRE_SOFFICE=1

# Default: run the test suite. `python -m handbook_generator.cli` builds the handbook.
CMD ["pytest", "-v"]
