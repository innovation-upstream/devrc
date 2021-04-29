#!/usr/bin/env bash

linkerd --context=$DEV_CLUSTER viz uninstall | kubectl delete -f -
linkerd --context=$DEV_CLUSTER multicluster uninstall | kubectl delete -f -
linkerd --context=$DEV_CLUSTER uninstall | kubectl delete -f -

