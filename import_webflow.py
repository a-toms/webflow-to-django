"""
Import Webflow files into Django project.

Example usage:
```bash
python scripts/import_webflow.py web export.webflow.zip
```
Note: you must be in the root of the project to run this script.
"""
import zipfile
import sys
from pathlib import Path
import os
import shutil
import logging
import re
from bs4 import BeautifulSoup, formatter
from send2trash import send2trash
from typing import List


make_forms_async = """
    // Add native form submission support for Webflow forms. This makes all forms asynchronous.
    async function submitForm(event, formElement) {
        /*
        We use this function to make static html forms submit data asynchronously.
        */
        event.preventDefault();

        const formData = new FormData(formElement);
        const response = await fetch(formElement.action, { method: formElement.method, body: formData });

        if (response.ok) {
            formElement.style.display = 'none';
            const formDoneElement = formElement.parentElement.querySelector('.w-form-done');
            if (formDoneElement) {
                formDoneElement.style.display = 'block';
            }
        } else {
            const formFailElement = formElement.parentElement.querySelector('.w-form-fail');
            if (formFailElement) {
                formFailElement.style.display = 'block';
            }
        }
    }

    const forms = document.querySelectorAll('form');
    forms.forEach(form => form.addEventListener('submit', event => submitForm(event, form)));
"""


class WebflowImporter:
    def __init__(self):
        self.app_name: str = None
        self.html_paths: List[str] = []
        self.static_files = {
            'js': [],
            'css': [],
            'images': [],
            'documents': [],
        }

    def move_static_file(self, work_dir: Path, target_app: str, static_file_type: str) -> None:
        """
        Move the static files from the Webflow export to the static files location of the
        Django target app.
        """
        dest_static_dir = f"{target_app}/static/{target_app}/{static_file_type}/"
        os.makedirs(dest_static_dir, exist_ok=True)

        source_static_dir = work_dir / static_file_type

        try:
            for file in os.listdir(source_static_dir.absolute()):
                file_src = os.path.join(source_static_dir, file)
                file_dest = os.path.join(dest_static_dir, file)
                os.rename(file_src, file_dest)
                sys.stdout.write(f'+ Moved {static_file_type} ({file} to {file_dest})\n')
                self.static_files[static_file_type].append(file)
        except FileNotFoundError:
            sys.stdout.write(f'No {static_file_type} files found. Continuing.\n')

    def move_html_files(self, work_dir: Path, target_app: str) -> None:
        """
        Move the html files from the Webflow export to the templates location of the
        Django target app.
        """
        dest_html_dir = f"{target_app}/templates/"
        os.makedirs(dest_html_dir, exist_ok=True)

        source_html_dir = work_dir
        for file in os.listdir(source_html_dir.absolute()):
            if file.endswith('.html'):
                file_src = os.path.join(source_html_dir, file)
                file_dest = os.path.join(dest_html_dir, file)
                os.rename(file_src, file_dest)
                sys.stdout.write(f'+ Moved html ({file} to {file_dest})\n')

                self.html_paths.append(file_dest)

    def update_htmls(self, target_app: str) -> None:
        """
        Update all the exported html files that we exported from Webflow
        to use the correct static file paths.
        """
        for html_path in self.html_paths:
            self.update_html(html_path, target_app)

    @staticmethod
    def update_html(html_path: str, target_app: str) -> None:
        """
        Update an html file to use the correct
        static files paths that work with Django.
        """
        with open(html_path, 'r') as f:
            soup = BeautifulSoup(f, 'html.parser')

        # Update the links element.
        for tag in soup.find_all('link'):
            if tag.get('href').startswith("css"):
                new_href = f"{{% static '{target_app}/{tag.get('href')}' %}}"
                tag["href"] = new_href
                sys.stdout.write(f'+ Updating link href to href=\"{new_href}\"\n')

            if tag.get('href').startswith("images"):
                new_href = f"{{% static '{target_app}/{tag.get('href')}' %}}"
                tag["href"] = new_href
                sys.stdout.write(f'+ Updating link href to href=\"{new_href}\"\n')

        # Update img elements.
        for tag in soup.find_all('img'):
            if tag.get('src').startswith("images"):  # I.e., unconverted.
                new_src = f"{{% static '{target_app}/{tag.get('src')}' %}}"
                tag["src"] = new_src
                sys.stdout.write(f'+ Updating img src to src=\"{new_src}\"\n')


            if tag.get('srcset', '').startswith("images"):
                new_srcset = re.sub(
                    r"images/([^ ]+)",
                    rf"{{% static '{target_app}/images/\g<1>' %}}",
                    tag.get('srcset')
                )
                tag["srcset"] = new_srcset
                sys.stdout.write(f'+ Updating img srcset to srcset=\"{new_srcset}\"\n')

        # Update js elements.
        for tag in soup.find_all('script'):
            src = tag.get('src')
            if src and src.startswith("js"):
                new_src = f"{{% static '{target_app}/{tag.get('src')}' %}}"
                tag["src"] = new_src
                sys.stdout.write(f'+ Updating script src to src=\"{new_src}\"\n')

        # Update the lottie animation elements.
        for tag in soup.find_all('div', {'data-animation-type': 'lottie'}):
            data_src = tag.get("data-src")
            if data_src and data_src.startswith("documents"):
                new_data_src = f"{{% static '{target_app}/{tag.get('data-src')}' %}}"
                tag["data-src"] = new_data_src
                sys.stdout.write(f'+ Updating lottie data-src to data-src=\"{new_data_src}\"\n')

        # Find any element (including divs and img) with a data-for attribute.

        # Convert any collection lists to for loops that render django template context.
        for tag in soup.find_all(['div', 'img', 'li', 'ul'], {'data-for': True}):
            for_loop_data = tag.get('data-for')

            if len(for_loop_data.split(" ")) > 1:  # E.g., "item in items"
                # Insert the django forloop.
                dj_forloop_tag = f"{{% for {for_loop_data} %}}"
                tag.insert(0, dj_forloop_tag)
                tag.insert(-1, "{% endfor %}")
                sys.stdout.write(f'+ Added for loop tag "{dj_forloop_tag}\"\n')
            
            else:  # E.g., "item.name"
                # Insert the django variable.
                if tag.name == 'img':
                    tag['src'] = f"{{{{ {for_loop_data} }}}}"
                else:
                    dj_variable = "{{ " + for_loop_data + " }}"
                    tag.insert(0, dj_variable)
                    sys.stdout.write(f'+ Added variable tag "{dj_variable}\"\n')

        # Insert the django static template tag.
        html_tag = soup.find('html')
        static_template_tag = '{% load static %}'
        html_tag.insert(0, static_template_tag)
        sys.stdout.write(f'+ Added  tag "{static_template_tag}\"\n')

        # Forms: Add the django csrf token to all forms.
        csrf_tag = '{% csrf_token %}'
        for form_tag in soup.find_all('form'):
            form_tag.insert(0, csrf_tag)
            sys.stdout.write(f'+ Added django csrf token to all forms.\n')

        # Forms: Make all forms async.
        script_tag = soup.new_tag('script')
        script_tag.append(make_forms_async)
        soup.body.append(script_tag)

        # Write the updated html file
        with open(html_path, 'w') as f:
            f.write(str(soup.prettify(formatter=formatter.HTMLFormatter(indent=4))))
            sys.stdout.write(f'Saved html file with updates to {html_path}\n')


if __name__ == "__main__":
    target_app = sys.argv[1]
    webflow_exported_assets = Path(sys.argv[2])
    working_folder = webflow_exported_assets.parent / "webflow_export"

    if not Path(target_app).exists():
        raise FileNotFoundError(f'App directory not found at: {target_app} .')

    shutil.unpack_archive(webflow_exported_assets, working_folder)

    importer = WebflowImporter()
    importer.move_static_file(working_folder, target_app, 'js')
    importer.move_static_file(working_folder, target_app, 'css')
    importer.move_static_file(working_folder, target_app, 'images')
    importer.move_static_file(working_folder, target_app, 'documents')
    importer.move_html_files(working_folder, target_app)
    importer.update_htmls(target_app)

    sys.stdout.write(f'Imported {webflow_exported_assets} to {target_app}.\n')
    send2trash(working_folder)

    delete_zip = str(input('Delete the zip file? (y/n): '))
    if delete_zip == 'y':
        send2trash(webflow_exported_assets)
        sys.stdout.write(f'Moved to trash: {webflow_exported_assets}.\n')
