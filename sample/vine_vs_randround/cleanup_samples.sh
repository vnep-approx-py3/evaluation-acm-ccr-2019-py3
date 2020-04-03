#!/bin/bash

pwd=$(pwd)
question="Are you sure you want to delete the content in this directory (${pwd}) [yY]?"
echo $question
read -n 1 -r
echo
if [[ $REPLY =~ ^[Yy]$ ]]
then
   rm -r input output plots log*
   rm *.pickle
fi
