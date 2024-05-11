#!/bin/bash

/usr/local/sbin/kea-admin db-version mysql -h $KEA_MYSQL_HOST -u $KEA_MYSQL_USERNAME -p $KEA_MYSQL_PASSWORD -n $KEA_MYSQL_DATABASE
if [ $? -ne 0 ] ; then
  /usr/local/sbin/kea-admin db-init mysql -h $KEA_MYSQL_HOST -u $KEA_MYSQL_USERNAME -p $KEA_MYSQL_PASSWORD -n $KEA_MYSQL_DATABASE
else
  /usr/local/sbin/kea-admin db-upgrade mysql -h $KEA_MYSQL_HOST -u $KEA_MYSQL_USERNAME -p $KEA_MYSQL_PASSWORD -n $KEA_MYSQL_DATABASE
fi
