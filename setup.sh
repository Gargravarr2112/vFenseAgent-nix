#!/bin/sh

if [ -d venv ]; then
    echo "Development environment already set up, skipping";
    exit 0;
else
    virtualenvBin=$(which virtualenv)
    if [ ! -x "$virtualenvBin" ]; then
        echo "Python virtualenv is not installed! Install through your package manager to continue!";
        exit 1;
    else
        $virtualenvBin venv;
        source venv/bin/activate;
        pip install --requirement pip-requirements.txt;
        echo "vFense Agent is now ready to use. Execute 'python agent/agent.py' from this shell (as root) to run."
    fi;
fi;