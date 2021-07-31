#!/usr/bin/env bash

gcloud container clusters get-credentials $SHARED_DEV_CLUSTER --zone us-central1-c

printf "\nMake sure to rename the new context with the folowing command so \
  devrc cluster scripts can find it! \n kubectl config rename-context \
  <cluster-name> \$SHARED_DEV_CLUSTER\n"
