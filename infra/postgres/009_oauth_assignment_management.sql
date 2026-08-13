GRANT INSERT,UPDATE ON app.ga_properties,app.ga_data_streams,app.website_analytics_assignments TO measurement_tenant;
INSERT INTO app.schema_migrations(version) VALUES('009_oauth_assignment_management') ON CONFLICT(version) DO NOTHING;
