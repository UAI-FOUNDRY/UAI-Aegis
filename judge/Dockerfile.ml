# Judge0 with the libraries Machine Learning / Data Science labs need.
#
# The 'python3-ml' language (and 'python3') run on Judge0's Python 3.8.1 (language id 71).
# This image adds NumPy, pandas, matplotlib, seaborn, scikit-learn and SciPy to it, pinned to
# the last versions that support Python 3.8. Use it for BOTH judge0 and judge0-workers (the
# workers are the ones that run code); docker-compose.yml already does.
#
# Not yet tested on a Judge0 server (no Docker on the development laptop): after building,
# run `python -m app.cli bml-lab` against the server; every question must validate.
FROM judge0/judge0:1.13.1

ARG PY=/usr/local/python-3.8.1/bin/python3

RUN ${PY} -m ensurepip --upgrade || true \
 && ${PY} -m pip install --no-cache-dir --upgrade pip \
 && ${PY} -m pip install --no-cache-dir \
      numpy==1.24.4 \
      pandas==2.0.3 \
      matplotlib==3.7.5 \
      seaborn==0.13.2 \
      scikit-learn==1.3.2 \
      scipy==1.10.1 \
 # Compile once at build time so the first exam run is not slowed down by it.
 && ${PY} -c "import numpy, pandas, matplotlib, seaborn, sklearn, scipy; print('ML libraries OK')"
