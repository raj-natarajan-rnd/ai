aws iam simulate-principal-policy \
  --policy-source-arn <your-user-or-role-arn> \
  --action-names \
    aoss:CreateCollection \
    aoss:BatchGetCollection \
    aoss:CreateSecurityPolicy \
    aoss:CreateAccessPolicy \
    aoss:APIAccessAll \
  --output table

For managed OpenSearch domains, swap in es:CreateDomain, es:DescribeDomain, es:ESHttpPut, es:ESHttpPost.

aws opensearchserverless list-collections
aws opensearch list-domain-names

