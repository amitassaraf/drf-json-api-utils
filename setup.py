from io import open
from os import path

# Always prefer setuptools over distutils
from setuptools import setup, find_packages

here = path.abspath(path.dirname(__file__))

# Get the long description from the README file
with open(path.join(here, 'README.md'), encoding='utf-8') as f:
    long_description = f.read()

setup(
    name='drf-json-api-utils',
    version='1.5.1',
    description='Utilities to reduce the boiler-plating of django-rest-framework-json-api',
    long_description=long_description,
    long_description_content_type='text/markdown',
    url='https://github.com/amitassaraf/drf-json-api-utils',
    author='Amit Assaraf',
    author_email='amit.assaraf@gmail.com',
    classifiers=[
        'Development Status :: 3 - Alpha',
        'Intended Audience :: Developers',
        'Topic :: Software Development :: Build Tools',
        'License :: OSI Approved :: MIT License',
        'Programming Language :: Python :: 3.5',
        'Programming Language :: Python :: 3.6',
        'Programming Language :: Python :: 3.7',
        'Programming Language :: Python :: 3.8',
    ],
    keywords='setuptools development',
    package_dir={'': 'src'},
    packages=find_packages(where='src'),
    python_requires='>=3.5',
    install_requires=['django>=3.0.0', 'djangorestframework-jsonapi', 'djangorestframework', 'django-filter',
                      'rest-framework-generic-relations==2.0.*'],
    extras_require={
        'dev': ['check-manifest'],
        'test': ['coverage'],
        'django-simple-history': ['django-simple-history'],
        'rest-framework-generic-relations': ['rest-framework-generic-relations']
    },
    entry_points={
        'console_scripts': [],
    },
    project_urls={
        'Bug Reports': 'https://github.com/amitassaraf/drf-json-api-utils/issues',
        'Source': 'https://github.com/amitassaraf/drf-json-api-utils/',
    },
)
