# markdown-to-static

Minimal Python static site generator. Reads Markdown files, converts them to HTML with a template, and writes the output to a build directory. Zero dependencies beyond the Python standard library.

## What it does

- Converts Markdown to HTML (headings, paragraphs, lists, code blocks, links, images, blockquotes, bold, italic)
- Parses simple front matter (`key: value` between `---` delimiters)
- Generates an automatic index page listing all posts sorted by date
- Copies static assets from a `static/` subdirectory
- Serves the built site locally for preview
- Creates a starter site with example content

No pip packages required. Uses only the Python standard library.

## Installation

```bash
git clone https://github.com/cappy-dev/markdown-to-static.git
cd markdown-to-static
chmod +x mds.py
```

Optional: add it to your PATH:

```bash
ln -s $(pwd)/mds.py ~/.local/bin/mds
```

## Quick start

Initialize a starter site:

```bash
python3 mds.py init my-site
```

Build it:

```bash
python3 mds.py build my-site/content my-site/output
```

Preview it locally:

```bash
python3 mds.py serve my-site/output
```

Then open http://localhost:8000 in your browser.

## Commands

### init

Create a starter site with example markdown files:

```bash
python3 mds.py init my-site
```

This creates `my-site/content/` with three example files: `index.md`, `hello.md`, and `about.md`.

### build

Build the static site from markdown content:

```bash
python3 mds.py build <content_dir> <output_dir>
```

- `content_dir`: Directory containing your `.md` files
- `output_dir`: Directory where HTML files get written (created if missing, cleaned if it exists)

Options:

- `--template FILE`: Path to a custom HTML template file
- `--index-template FILE`: Path to a custom index page template file
- `--base-url URL`: Base URL for the site (used in meta tags)

### serve

Serve the built site locally:

```bash
python3 mds.py serve <directory> [--port 8000]
```

Starts a simple HTTP server. Press Ctrl+C to stop.

## Front matter

Each markdown file can include front matter at the top:

```
---
title: My Page Title
date: 2026-06-27
slug: custom-url
lang: en
---

Page content here.
```

Supported fields:

- `title`: Page title. Falls back to the filename with dashes replaced by spaces.
- `date`: Publication date. Used for sorting on the index page.
- `slug`: URL-friendly filename for the output HTML. Falls back to a slugified version of the title.
- `lang`: HTML lang attribute. Defaults to "en".

## Markdown support

The built-in parser handles:

- Headings: `# h1` through `###### h6`
- Paragraphs: consecutive lines of text
- Bold: `**text**` or `__text__`
- Italic: `*text*` or `_text_`
- Links: `[text](url)`
- Images: `![alt](url)`
- Inline code: `` `code` ``
- Fenced code blocks with language class: ` ```python ``
- Unordered lists: `- item` or `* item`
- Ordered lists: `1. item`
- Blockquotes: `> quote`
- Horizontal rules: `---`

Not supported: tables, footnotes, definition lists, task lists. If you need those, write raw HTML in your markdown.

## Directory structure

Example content directory:

```
content/
  index.md        # optional site index page
  hello.md        # a blog post
  about.md        # another page
  static/
    style.css     # copied as-is to output/static/
```

The builder outputs:

```
output/
  index.html      # auto-generated index (if no index.md exists)
  hello-world.html
  about.html
  static/
    style.css
```

## Custom templates

### Page template

Create an HTML file with `{{title}}`, `{{content}}`, and `{{lang}}` placeholders:

```html
<!DOCTYPE html>
<html lang="{{lang}}">
<head>
  <title>{{title}}</title>
  <link rel="stylesheet" href="/static/style.css">
</head>
<body>
  <article>{{content}}</article>
</body>
</html>
```

Build with:

```bash
python3 mds.py build content output --template my-template.html
```

### Index template

Same idea, but the index template uses `{{post_list}}` instead of `{{content}}`:

```html
<ul>
{{post_list}}
</ul>
```

Build with:

```bash
python3 mds.py build content output --index-template my-index-template.html
```

## Automation

Run the build on file changes with a simple loop:

```bash
while true; do
  python3 mds.py build content output
  sleep 5
done
```

Or add it to a CI pipeline that builds and deploys on every push.

## Why another static site generator?

Most static site generators need Node.js, Ruby, or a long list of Python packages. Sometimes you just want a script that reads some markdown and writes some HTML. That is all this is.

## License

MIT
