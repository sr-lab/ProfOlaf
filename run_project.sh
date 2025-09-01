#!/bin/bash

echo "Generating snowball start"
python 0_generate_snowball_start.py test_files/pre_snowball_start.txt --iteration 1
echo "Starting iteration"
python 1_start_iteration.py --iteration 1
echo "Getting bibtex"
python 2_get_bibtex.py --iteration 1
#echo "Generating conf rank"
#python 3_generate_conf_rank.py --iteration 1
echo "Done"