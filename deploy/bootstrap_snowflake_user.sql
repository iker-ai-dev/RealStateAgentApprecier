-- Run once in the existing Snowflake account with a user-administration role.
-- This contains only the public key. The private key is held in Actions secrets.
USE ROLE SECURITYADMIN;

CREATE USER RETAILSCOPE_DEPLOY
    TYPE = SERVICE
    LOGIN_NAME = 'RETAILSCOPE_DEPLOY'
    DEFAULT_ROLE = 'ATTENDEE_ROLE'
    DEFAULT_NAMESPACE = 'DEFAULT_DATABASE.DEFAULT_SCHEMA'
    RSA_PUBLIC_KEY = 'MIIBIjANBgkqhkiG9w0BAQEFAAOCAQ8AMIIBCgKCAQEAyRnIBKR3ZHWWYdTGpu3aFf4kiTD7jhww2xjQRvMOKE09W3B/URzNivvJm8Vaw3dUyj2KRGNZfs7BLrpUgt/uoNSJTwV6m+Q1oVY4iKcvo8TfbSmsVTYcsVLkROSD3xJlfs6ehiam8FtiOXabhK/IbCR9GfPmLwOV64WvchR3WjBZgeUf2jdtD1AeCOQoH7FMmOlx0/TeThZ9VB5qx370tQzmMDjc9y6hBOVzi1ivgVz7qMzePtfHu5eJXM8fp7N6mU92oPWhZmBL+ngXOUCfjiDTNlFNn7by1P51gD63pm3NiIPu8tqWR617RxCIjtvvBY0HsCpBuUOkGeRzhSIvUwIDAQAB'
    COMMENT = 'RetailScope GitHub Actions deployment service account';

-- Reuse the role used by the existing deployment so app ownership stays intact.
GRANT ROLE ATTENDEE_ROLE TO USER RETAILSCOPE_DEPLOY;
DESCRIBE USER RETAILSCOPE_DEPLOY;
SHOW WAREHOUSES;
