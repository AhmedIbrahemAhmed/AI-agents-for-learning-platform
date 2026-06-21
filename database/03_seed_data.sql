-- ─────────────────────────────────────────────────────────────
-- GraduationProject: Sample Seed Data
-- ─────────────────────────────────────────────────────────────
-- Run this script AFTER 01_sql_schema.sql to populate the database
-- with sample users, topics, relationships, and mastery records.
-- ─────────────────────────────────────────────────────────────

USE [GraduationProject]
GO

-- ─────────────────────────────────────────────────────────────
-- 1. Insert Users
-- ─────────────────────────────────────────────────────────────
INSERT INTO [dbo].[Users] (Name, Email)
VALUES 
  ('Ahmed', 'ahmed@example.com'),
  ('Sara', 'sara@example.com'),
  ('Ali', 'ali@example.com');

DECLARE @Ahmed_UserId BIGINT, @Sara_UserId BIGINT, @Ali_UserId BIGINT;
SELECT @Ahmed_UserId = UserId FROM Users WHERE Email = 'ahmed@example.com';
SELECT @Sara_UserId = UserId FROM Users WHERE Email = 'sara@example.com';
SELECT @Ali_UserId = UserId FROM Users WHERE Email = 'ali@example.com';

-- ─────────────────────────────────────────────────────────────
-- 2. Insert Domain Topics
-- ─────────────────────────────────────────────────────────────
INSERT INTO [dbo].[Topics] (Name, Type, Difficulty, EstimatedHours, Description)
VALUES 
  ('Machine Learning', 'Domain', 3, 40.00, 'Core ML concepts and algorithms'),
  ('Statistics', 'Domain', 2, 30.00, 'Foundational statistics and probability'),
  ('Web Development', 'Domain', 2, 50.00, 'Frontend and backend web technologies');

DECLARE @ML_DomainId INT, @Stats_DomainId INT, @WebDev_DomainId INT;
SELECT @ML_DomainId = TopicId FROM Topics WHERE Name = 'Machine Learning';
SELECT @Stats_DomainId = TopicId FROM Topics WHERE Name = 'Statistics';
SELECT @WebDev_DomainId = TopicId FROM Topics WHERE Name = 'Web Development';

-- ─────────────────────────────────────────────────────────────
-- 3. Insert Child Topics (Concepts)
-- ─────────────────────────────────────────────────────────────
INSERT INTO [dbo].[Topics] (Name, Type, Difficulty, EstimatedHours, Description)
VALUES 
  ('Neural Networks', 'Concept', 3, 20.00, 'Deep learning and NN fundamentals'),
  ('Decision Trees', 'Concept', 2, 10.00, 'Tree-based learning methods'),
  ('Random Forest', 'Concept', 3, 15.00, 'Ensemble methods with trees'),
  ('Probability Distribution', 'Concept', 2, 8.00, 'Understanding probability distributions'),
  ('Hypothesis Testing', 'Concept', 2, 6.00, 'Statistical hypothesis testing'),
  ('Bayes Theorem', 'Concept', 3, 7.00, 'Bayesian inference and Bayes rule'),
  ('HTML & CSS', 'Concept', 1, 15.00, 'Web markup and styling'),
  ('JavaScript', 'Concept', 2, 25.00, 'Client-side scripting language');

DECLARE 
  @NN_Id INT, @DT_Id INT, @RF_Id INT,
  @Prob_Id INT, @HypTest_Id INT, @Bayes_Id INT,
  @HTML_Id INT, @JS_Id INT;

SELECT @NN_Id = TopicId FROM Topics WHERE Name = 'Neural Networks';
SELECT @DT_Id = TopicId FROM Topics WHERE Name = 'Decision Trees';
SELECT @RF_Id = TopicId FROM Topics WHERE Name = 'Random Forest';
SELECT @Prob_Id = TopicId FROM Topics WHERE Name = 'Probability Distribution';
SELECT @HypTest_Id = TopicId FROM Topics WHERE Name = 'Hypothesis Testing';
SELECT @Bayes_Id = TopicId FROM Topics WHERE Name = 'Bayes Theorem';
SELECT @HTML_Id = TopicId FROM Topics WHERE Name = 'HTML & CSS';
SELECT @JS_Id = TopicId FROM Topics WHERE Name = 'JavaScript';

-- ─────────────────────────────────────────────────────────────
-- 4. Insert Topic Relationships
-- ─────────────────────────────────────────────────────────────
INSERT INTO [dbo].[TopicRelationships] (SourceTopicId, TargetTopicId, RelationshipType, Weight)
VALUES 
  -- Machine Learning contains...
  (@ML_DomainId, @NN_Id, 'contains', 0.35),
  (@ML_DomainId, @DT_Id, 'contains', 0.30),
  (@ML_DomainId, @RF_Id, 'contains', 0.35),
  -- Statistics contains...
  (@Stats_DomainId, @Prob_Id, 'contains', 0.50),
  (@Stats_DomainId, @HypTest_Id, 'contains', 0.30),
  (@Stats_DomainId, @Bayes_Id, 'contains', 0.20),
  -- Web Development contains...
  (@WebDev_DomainId, @HTML_Id, 'contains', 0.40),
  (@WebDev_DomainId, @JS_Id, 'contains', 0.60),
  -- Prerequisites
  (@ML_DomainId, @Prob_Id, 'prerequisite_for', 0.40);

-- ─────────────────────────────────────────────────────────────
-- 5. Initialize User Topic Mastery
-- ─────────────────────────────────────────────────────────────
INSERT INTO [dbo].[UserTopicMastery] 
  (UserId, TopicId, Mastery, Confidence, Interest, EvidenceCount, LastUpdated)
VALUES 
  -- Ahmed: interested in ML
  (@Ahmed_UserId, @ML_DomainId, 0.00, 0.00, 0.80, 0, GETUTCDATE()),
  (@Ahmed_UserId, @Stats_DomainId, 0.00, 0.00, 0.50, 0, GETUTCDATE()),
  (@Ahmed_UserId, @WebDev_DomainId, 0.00, 0.00, 0.30, 0, GETUTCDATE()),
  (@Ahmed_UserId, @NN_Id, 0.00, 0.00, 0.70, 0, GETUTCDATE()),
  (@Ahmed_UserId, @DT_Id, 0.00, 0.00, 0.50, 0, GETUTCDATE()),
  (@Ahmed_UserId, @RF_Id, 0.00, 0.00, 0.50, 0, GETUTCDATE()),
  (@Ahmed_UserId, @Prob_Id, 0.00, 0.00, 0.40, 0, GETUTCDATE()),
  (@Ahmed_UserId, @HypTest_Id, 0.00, 0.00, 0.40, 0, GETUTCDATE()),
  (@Ahmed_UserId, @Bayes_Id, 0.00, 0.00, 0.40, 0, GETUTCDATE()),
  -- Sara: interested in Web Dev
  (@Sara_UserId, @ML_DomainId, 0.00, 0.00, 0.30, 0, GETUTCDATE()),
  (@Sara_UserId, @Stats_DomainId, 0.00, 0.00, 0.30, 0, GETUTCDATE()),
  (@Sara_UserId, @WebDev_DomainId, 0.00, 0.00, 0.90, 0, GETUTCDATE()),
  (@Sara_UserId, @HTML_Id, 0.00, 0.00, 0.85, 0, GETUTCDATE()),
  (@Sara_UserId, @JS_Id, 0.00, 0.00, 0.80, 0, GETUTCDATE()),
  -- Ali: interested in Statistics
  (@Ali_UserId, @ML_DomainId, 0.00, 0.00, 0.40, 0, GETUTCDATE()),
  (@Ali_UserId, @Stats_DomainId, 0.00, 0.00, 0.85, 0, GETUTCDATE()),
  (@Ali_UserId, @Prob_Id, 0.00, 0.00, 0.80, 0, GETUTCDATE()),
  (@Ali_UserId, @HypTest_Id, 0.00, 0.00, 0.75, 0, GETUTCDATE()),
  (@Ali_UserId, @Bayes_Id, 0.00, 0.00, 0.70, 0, GETUTCDATE());

-- ─────────────────────────────────────────────────────────────
-- 6. Insert Sample Resources
-- ─────────────────────────────────────────────────────────────
INSERT INTO [dbo].[Resources] (Title, Type, Url, Difficulty, Depth, EstimatedMinutes)
VALUES 
  ('Neural Networks Fundamentals', 'Youtube', 'https://www.youtube.com/watch?v=example_nn', 3, 2, 45),
  ('Decision Trees Explained', 'Youtube', 'https://www.youtube.com/watch?v=example_dt', 2, 2, 30),
  ('Probability 101', 'Article', 'https://example.com/probability-101', 2, 1, 20),
  ('HTML for Beginners', 'Course', 'https://example.com/html-beginners', 1, 1, 120),
  ('JavaScript Advanced', 'Book', 'https://example.com/js-advanced', 3, 3, 600);

DECLARE @NN_Resource_Id BIGINT, @DT_Resource_Id BIGINT, @Prob_Resource_Id BIGINT, 
        @HTML_Resource_Id BIGINT, @JS_Resource_Id BIGINT;

SELECT @NN_Resource_Id = ResourceId FROM Resources WHERE Title = 'Neural Networks Fundamentals';
SELECT @DT_Resource_Id = ResourceId FROM Resources WHERE Title = 'Decision Trees Explained';
SELECT @Prob_Resource_Id = ResourceId FROM Resources WHERE Title = 'Probability 101';
SELECT @HTML_Resource_Id = ResourceId FROM Resources WHERE Title = 'HTML for Beginners';
SELECT @JS_Resource_Id = ResourceId FROM Resources WHERE Title = 'JavaScript Advanced';

-- ─────────────────────────────────────────────────────────────
-- 7. Link Resources to Topics
-- ─────────────────────────────────────────────────────────────
INSERT INTO [dbo].[ResourceTopicCoverage] (ResourceId, TopicId, CoverageWeight, DifficultyContribution)
VALUES 
  (@NN_Resource_Id, @NN_Id, 0.70, 0.80),
  (@NN_Resource_Id, @ML_DomainId, 0.30, 0.60),
  (@DT_Resource_Id, @DT_Id, 0.60, 0.70),
  (@DT_Resource_Id, @RF_Id, 0.40, 0.50),
  (@Prob_Resource_Id, @Prob_Id, 0.80, 0.70),
  (@Prob_Resource_Id, @Stats_DomainId, 0.20, 0.40),
  (@HTML_Resource_Id, @HTML_Id, 0.90, 0.80),
  (@JS_Resource_Id, @JS_Id, 0.85, 0.90);

-- ─────────────────────────────────────────────────────────────
-- 8. Sample Projects, Certificates, Experiences, Educations for testing (Ahmed)
-- Use the previously set @Ahmed_UserId variable
-- ─────────────────────────────────────────────────────────────

INSERT INTO [dbo].[Projects] (UserId, Title, Description, Url, StartDate, EndDate, Role, Technologies)
VALUES
  (@Ahmed_UserId, 'Personal ML Portfolio', 'Implemented image classification models and deployed as REST API', 'https://github.com/ahmed/ml-portfolio', '2023-06-01', '2024-01-31', 'Lead Developer', 'PyTorch, FastAPI, Docker'),
  (@Ahmed_UserId, 'Decision Tree Visualizer', 'Web app for building and visualizing decision trees', 'https://github.com/ahmed/dt-visualizer', '2022-09-01', '2022-12-15', 'Full Stack Developer', 'React, Flask');

INSERT INTO [dbo].[Certificates] (UserId, Name, Issuer, IssueDate, CredentialId, Url, Description)
VALUES
  (@Ahmed_UserId, 'Machine Learning Specialization', 'Coursera', '2023-02-15', 'MLSP-2023-0001', 'https://coursera.org/cert/MLSP-2023-0001', 'Completed a series of ML courses covering supervised and unsupervised learning'),
  (@Ahmed_UserId, 'Deep Learning Nanodegree', 'Udacity', '2024-03-10', 'DLND-2024-045', 'https://udacity.com/cert/DLND-2024-045', 'Projects in computer vision and sequence modeling');

INSERT INTO [dbo].[Experiences] (UserId, Company, Role, StartDate, EndDate, Location, Description, [Current], SortOrder)
VALUES
  (@Ahmed_UserId, 'DataSense LLC', 'Machine Learning Engineer', '2024-02-01', NULL, 'Cairo, Egypt', 'Worked on classification models for document processing and end-to-end model deployment', 1, 1),
  (@Ahmed_UserId, 'WebWorks', 'Software Engineer', '2021-05-01', '2023-01-31', 'Cairo, Egypt', 'Built front-end interfaces and backend services for client web applications', 0, 2);

INSERT INTO [dbo].[Educations] (UserId, Institution, Degree, Field, StartDate, EndDate, Location, Description, SortOrder)
VALUES
  (@Ahmed_UserId, 'Cairo University', 'B.Sc. Computer Science', 'Computer Science', '2017-09-01', '2021-06-30', 'Cairo, Egypt', 'Graduated with honors; final year project on neural network optimization', 1),
  (@Ahmed_UserId, 'Online - Coursera', 'Professional Certificate', 'Machine Learning', '2022-01-01', '2023-02-15', NULL, 'Completed the Machine Learning Specialization coursework', 2);

PRINT '✓ Seed data inserted successfully.'
PRINT '  - 3 users created'
PRINT '  - 3 domain topics created'
PRINT '  - 8 concept topics created'
PRINT '  - 5 sample resources created'
PRINT '  - User mastery initialized for all users and topics'
